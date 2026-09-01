import type { ConfigProvider, LoadDeclaration, LoadProfile, CatalogItem, FillLevel, VehicleClass } from '@moveon/shared';
import { findVehicleClass } from '@moveon/shared';
import { UnknownCatalogItemError, UnknownVehicleClassError } from './errors.js';

/**
 * The load estimation system (§4). Four layers, each normalising down to one
 * `LoadProfile` so the pricing engine only ever sees volume, mass and flags.
 *
 * A customer is never asked for kilograms at any layer. Weight is inferred —
 * from catalog data, or from a nominal density — purely so the system can
 * enforce the vehicle's legal payload ceiling on their behalf.
 */

const ROUND = (n: number, dp = 3): number => Math.round(n * 10 ** dp) / 10 ** dp;

export function resolveLoad(config: ConfigProvider, declaration: LoadDeclaration): LoadProfile {
  switch (declaration.method) {
    case 'catalog':
      return fromCatalogBasket(config, declaration.lines);
    case 'fill_level':
      return fromFillLevel(config, declaration.referenceClass, declaration.fillLevel);
    case 'photo':
      return fromPhotoEstimate(config, declaration);
  }
}

/* ------------------------------------------------------------------ */
/* Layer 1 — item catalog (~80% of bookings)                           */
/* ------------------------------------------------------------------ */

export function fromCatalogBasket(
  config: ConfigProvider,
  lines: readonly { itemId: string; quantity: number }[],
): LoadProfile {
  const catalog = new Map(config.catalog().map((item) => [item.id, item]));

  let volumeM3 = 0;
  let weightKg = 0;
  let handlingUnits = 0;
  let fragile = false;
  let requiresEnclosed = false;
  let densityCritical = false;
  let minBedLengthM = 0;
  let pieces = 0;

  for (const line of lines) {
    const item: CatalogItem | undefined = catalog.get(line.itemId);
    if (!item) throw new UnknownCatalogItemError(line.itemId);

    volumeM3 += item.volumeM3 * line.quantity;
    weightKg += item.weightKg * line.quantity;
    handlingUnits += item.handlingUnits * line.quantity;
    pieces += line.quantity;
    if (item.fragile) fragile = true;
    if (item.requiresEnclosed) requiresEnclosed = true;
    if (item.densityCritical) densityCritical = true;
    // The longest single item sets the bed we need. Lengths never sum.
    if (item.minBedLengthM !== undefined && item.minBedLengthM > minBedLengthM) minBedLengthM = item.minBedLengthM;
  }

  return {
    volumeM3: ROUND(volumeM3),
    weightKg: ROUND(weightKg, 1),
    fragile,
    requiresEnclosed,
    handlingUnits,
    densityCritical,
    minBedLengthM,
    summary: `Your ${pieces} ${pieces === 1 ? 'item needs' : 'items need'} ~${ROUND(volumeM3, 1)} m³`,
  };
}

/** Expand a one-tap bundle ("1-bedroom house move") into basket lines. */
export function expandBundle(config: ConfigProvider, bundleId: string): { itemId: string; quantity: number }[] {
  const bundle = config.bundles().find((b) => b.id === bundleId);
  if (!bundle) throw new UnknownCatalogItemError(bundleId);
  return bundle.items.map((line) => ({ itemId: line.itemId, quantity: line.quantity }));
}

/* ------------------------------------------------------------------ */
/* Layer 2 — fill-level slider (fallback)                              */
/* ------------------------------------------------------------------ */

export function fromFillLevel(config: ConfigProvider, referenceClassCode: string, fillLevel: FillLevel): LoadProfile {
  const reference: VehicleClass | undefined = findVehicleClass(config, referenceClassCode);
  if (!reference) throw new UnknownVehicleClassError(referenceClassCode);

  const { fillLevelFractions, nominalDensityKgPerM3, handlingUnitsPerM3 } = config.pricing().estimation;
  const fraction = fillLevelFractions[fillLevel];
  const volumeM3 = reference.volumeM3 * fraction;
  // No item data, so mass is inferred from a household-goods density. Crude,
  // honest, fast — and the payload ceiling is still checked against it.
  const weightKg = Math.min(volumeM3 * nominalDensityKgPerM3, reference.payloadKg * fraction);

  return {
    volumeM3: ROUND(volumeM3),
    weightKg: ROUND(weightKg, 1),
    fragile: false,
    requiresEnclosed: false,
    handlingUnits: Math.max(1, Math.ceil(volumeM3 * handlingUnitsPerM3)),
    densityCritical: false,
    minBedLengthM: 0,
    summary: `About ${describeFill(fillLevel)} of a ${reference.displayName} (~${ROUND(volumeM3, 1)} m³)`,
  };
}

function describeFill(level: FillLevel): string {
  switch (level) {
    case 'quarter': return 'a quarter';
    case 'half': return 'half';
    case 'three_quarter': return 'three quarters';
    case 'full': return 'a full load';
    case 'overflowing': return 'more than a full load';
  }
}

/* ------------------------------------------------------------------ */
/* Layer 3 — photo estimate (differentiator)                           */
/* ------------------------------------------------------------------ */

export function fromPhotoEstimate(
  config: ConfigProvider,
  estimate: { estimatedVolumeM3: number; estimatedWeightKg: number; confidence: number; requiresEnclosed: boolean; fragile: boolean },
): LoadProfile {
  const { handlingUnitsPerM3 } = config.pricing().estimation;
  return {
    volumeM3: ROUND(estimate.estimatedVolumeM3),
    weightKg: ROUND(estimate.estimatedWeightKg, 1),
    fragile: estimate.fragile,
    requiresEnclosed: estimate.requiresEnclosed,
    handlingUnits: Math.max(1, Math.ceil(estimate.estimatedVolumeM3 * handlingUnitsPerM3)),
    densityCritical: false,
    minBedLengthM: 0,
    summary: `From your photos: about ${ROUND(estimate.estimatedVolumeM3, 1)} m³`,
  };
}

/**
 * A photo estimate is a *suggestion the customer confirms*, never a silent
 * decision. This decides how the app should present it.
 */
export function assessPhotoConfidence(
  config: ConfigProvider,
  confidence: number,
): { presentation: 'suggest' | 'fall_back'; message: string } {
  const threshold = config.pricing().estimation.photoMinConfidence;
  if (confidence >= threshold) {
    return { presentation: 'suggest', message: 'We think this is what you are moving. Check it before you book.' };
  }
  return {
    presentation: 'fall_back',
    message: "We couldn't read the pile clearly. Pick your items, or use the fill slider.",
  };
}

/**
 * Every photo estimate is logged against the load the driver eventually
 * confirmed. That pairing is the training set, and nobody else has it (§4).
 */
export interface PhotoEstimateObservation {
  photoIds: string[];
  estimatedVolumeM3: number;
  confidence: number;
  confirmedVolumeM3: number;
  confirmedVehicleClass: string;
  observedAt: string;
}

export function buildPhotoObservation(
  estimate: { photoIds: string[]; estimatedVolumeM3: number; confidence: number },
  truth: { confirmedVolumeM3: number; confirmedVehicleClass: string },
  observedAt: Date,
): PhotoEstimateObservation {
  return {
    photoIds: estimate.photoIds,
    estimatedVolumeM3: estimate.estimatedVolumeM3,
    confidence: estimate.confidence,
    confirmedVolumeM3: truth.confirmedVolumeM3,
    confirmedVehicleClass: truth.confirmedVehicleClass,
    observedAt: observedAt.toISOString(),
  };
}
