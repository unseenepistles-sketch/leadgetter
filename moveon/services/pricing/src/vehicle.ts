import type { ConfigProvider, LoadProfile, VehicleClass } from '@moveon/shared';
import { classesBySize } from '@moveon/shared';
import { NoSuitableVehicleError } from './errors.js';

/**
 * Vehicle selection (§4, §7).
 *
 * Fit is a HARD FILTER, never a soft score. Dispatching a pickup to a 9 m³ load
 * is the exact failure mode that kills generalist ride apps entering cargo, and
 * it is prevented here rather than apologised for at the roadside.
 *
 * Every load is checked against BOTH constraints — volume and payload — because
 * twenty sacks of cement fill a fifth of a canter and still exceed what a pickup
 * may legally carry.
 */

export interface FitContext {
  passengers: number;
}

export type FitFailure =
  | 'volume'
  | 'payload'
  | 'bed_length'
  | 'enclosed'
  | 'passengers';

export interface FitAssessment {
  vehicleClass: VehicleClass;
  fits: boolean;
  failures: FitFailure[];
  /** How full by space. This is the fill fraction the customer is shown. */
  volumeFraction: number;
  /** How close to the legal ceiling. The customer never sees this; the system enforces it. */
  weightFraction: number;
  /**
   * The constraint that actually commits the vehicle — whichever of space or
   * mass is higher. A canter at 12% full but 67% of its payload is two-thirds
   * committed, and pricing it on empty space would pay the driver for a job
   * they cannot double up on.
   */
  bindingFraction: number;
  /** Payload ceiling reached before the bed filled — cement, blocks, tiles. */
  densityCritical: boolean;
}

export function assessFit(
  config: ConfigProvider,
  vehicleClass: VehicleClass,
  load: LoadProfile,
  context: FitContext,
): FitAssessment {
  const failures: FitFailure[] = [];

  const volumeFraction = load.volumeM3 / vehicleClass.volumeM3;
  const weightFraction = load.weightKg / vehicleClass.payloadKg;

  if (volumeFraction > 1) failures.push('volume');
  if (weightFraction > 1) failures.push('payload');
  if (load.minBedLengthM > vehicleClass.bedLengthM) failures.push('bed_length');
  if (load.requiresEnclosed && !vehicleClass.enclosed) failures.push('enclosed');
  if (context.passengers > vehicleClass.passengerCapacity) failures.push('passengers');

  const { densityCriticalThreshold } = config.pricing().loadFactor;

  return {
    vehicleClass,
    fits: failures.length === 0,
    failures,
    volumeFraction: round(volumeFraction),
    weightFraction: round(weightFraction),
    bindingFraction: round(Math.max(volumeFraction, weightFraction)),
    // "Payload ceiling reached before volume" — mass is the binding constraint,
    // and it is close enough to the ceiling to matter.
    densityCritical: weightFraction > volumeFraction && weightFraction >= densityCriticalThreshold,
  };
}

export interface Recommendation {
  assessment: FitAssessment;
  /** Loads too big for anything available are split, not refused. */
  tripsRequired: number;
  reason: string;
}

/**
 * The smallest available vehicle that can legally and physically take the load.
 * Smallest, because over-vehicling is how you price yourself out of a market.
 */
export function recommendVehicle(
  config: ConfigProvider,
  load: LoadProfile,
  context: FitContext,
  options: { onlyEnabled?: boolean } = {},
): Recommendation {
  const candidates = classesBySize(config, { onlyEnabled: options.onlyEnabled ?? true });
  const assessments = candidates.map((vehicleClass) => assessFit(config, vehicleClass, load, context));

  const fitting = assessments.find((a) => a.fits);
  if (fitting) {
    return { assessment: fitting, tripsRequired: 1, reason: describeReason(load, fitting, 1) };
  }

  // Nothing fits in one trip. Some failures can be solved by splitting the load
  // across trips; others cannot — a 6 m pipe does not become shorter.
  const largest = assessments[assessments.length - 1];
  if (!largest) throw new NoSuitableVehicleError(['no vehicle classes are available']);

  const splitCandidate = assessments.find((a) => !a.failures.some(isUnsplittable));
  if (!splitCandidate) throw new NoSuitableVehicleError(explainFailures(largest, load, context));

  const tripsRequired = Math.max(
    1,
    Math.ceil(splitCandidate.volumeFraction),
    Math.ceil(splitCandidate.weightFraction),
  );
  const perTrip: FitAssessment = {
    ...splitCandidate,
    fits: true,
    failures: [],
    volumeFraction: round(splitCandidate.volumeFraction / tripsRequired),
    weightFraction: round(splitCandidate.weightFraction / tripsRequired),
    bindingFraction: round(splitCandidate.bindingFraction / tripsRequired),
  };
  return { assessment: perTrip, tripsRequired, reason: describeReason(load, perTrip, tripsRequired) };
}

/** Splitting the load across trips cannot fix these. */
function isUnsplittable(failure: FitFailure): boolean {
  return failure === 'bed_length' || failure === 'enclosed' || failure === 'passengers';
}

/** Every failure on the largest vehicle, in words ops can read to a customer. */
function explainFailures(largest: FitAssessment, load: LoadProfile, context: FitContext): string[] {
  return largest.failures.map((failure) => {
    switch (failure) {
      case 'volume': return 'the load is larger than every available vehicle';
      case 'payload': return 'the load is heavier than any available vehicle may legally carry';
      case 'bed_length': return `nothing available has a bed of ${load.minBedLengthM} m`;
      case 'enclosed': return 'this load needs an enclosed vehicle and none is available';
      case 'passengers': return `no available vehicle seats ${context.passengers} passenger(s) alongside the load`;
    }
  });
}

function describeReason(load: LoadProfile, assessment: FitAssessment, trips: number): string {
  const tripNote = trips > 1 ? ` across ${trips} trips` : '';
  if (assessment.densityCritical) {
    return `${load.summary} — but the weight fills a ${assessment.vehicleClass.displayName} first${tripNote}`;
  }
  return `${load.summary} — that's a ${assessment.vehicleClass.displayName}${tripNote}`;
}

function round(n: number): number {
  return Math.round(n * 10000) / 10000;
}
