import type {
  ConfigProvider,
  LoadProfile,
  ParsedQuoteRequest,
  Quote,
  QuoteAlternative,
  QuoteLine,
  VehicleClass,
  ClassPricing,
} from '@moveon/shared';
import { findClassPricing, findVehicleClass, classesBySize } from '@moveon/shared';
import { priceDistance } from './distance.js';
import { resolveLoad } from './load.js';
import { assessFit, recommendVehicle, type FitAssessment } from './vehicle.js';
import { UnknownVehicleClassError, PricingError } from './errors.js';

/**
 * The pricing engine (§6).
 *
 * PURE. Input: a quote request plus everything time- or identity-dependent that
 * the caller must supply explicitly. Output: a quote object. No DB writes, no
 * network calls, no clock reads, no randomness. This is where money leaks, so
 * it is the one service that can be reasoned about entirely from its inputs.
 *
 *   quote = base
 *         + distance (tapered)
 *         + time in traffic
 *         + load factor
 *         + surcharges
 *         - discounts
 */

export interface QuoteContext {
  /** Supplied, never read from the clock — a pure function has no `now`. */
  now: Date;
  quoteId: string;
  /** Anti-gaming state for this customer (§4). Absent means a clean record. */
  customerRisk?: { underDeclarationRate: number; observations: number };
}

export function priceQuote(config: ConfigProvider, request: ParsedQuoteRequest, context: QuoteContext): Quote {
  const load = resolveLoad(config, request.load);
  const pricing = config.pricing();

  const { vehicleClass, assessment, tripsRequired, reason } = selectVehicle(config, request, load);
  const priced = priceForClass(config, request, load, vehicleClass, assessment, tripsRequired);

  const risk = evaluateCustomerRisk(config, context.customerRisk);

  return {
    quoteId: context.quoteId,
    expiresAt: new Date(context.now.getTime() + pricing.quoteTtlSeconds * 1000).toISOString(),
    vehicleClass: vehicleClass.code,
    recommendedReason: reason,
    total: priced.total,
    currency: pricing.currency,
    breakdown: priced.breakdown,
    included: buildIncludedList(config, vehicleClass),
    alternatives: buildAlternatives(config, request, load, vehicleClass, priced.total),
    meta: {
      volumeM3: load.volumeM3,
      weightKg: load.weightKg,
      fillFraction: assessment.volumeFraction,
      weightFraction: assessment.weightFraction,
      densityCritical: assessment.densityCritical,
      loadFactorMultiplier: priced.loadFactorMultiplier,
      freeHandlingMinutes: vehicleClass.freeHandlingMinutes,
      toleranceFraction: risk.toleranceFraction,
      requiresPhotoEstimate: risk.requiresPhotoEstimate,
      requiresPrepay: risk.requiresPrepay,
    },
  };
}

/* ------------------------------------------------------------------ */
/* Vehicle selection                                                   */
/* ------------------------------------------------------------------ */

function selectVehicle(config: ConfigProvider, request: ParsedQuoteRequest, load: LoadProfile): {
  vehicleClass: VehicleClass;
  assessment: FitAssessment;
  tripsRequired: number;
  reason: string;
} {
  if (request.forceVehicleClass !== undefined) {
    const forced = findVehicleClass(config, request.forceVehicleClass);
    if (!forced) throw new UnknownVehicleClassError(request.forceVehicleClass);
    const assessment = assessFit(config, forced, load, { passengers: request.passengers });
    return {
      vehicleClass: forced,
      assessment,
      tripsRequired: 1,
      reason: `${load.summary} — ${forced.displayName} selected`,
    };
  }

  const recommendation = recommendVehicle(config, load, { passengers: request.passengers });
  return {
    vehicleClass: recommendation.assessment.vehicleClass,
    assessment: recommendation.assessment,
    tripsRequired: recommendation.tripsRequired,
    reason: recommendation.reason,
  };
}

/* ------------------------------------------------------------------ */
/* The calculation                                                     */
/* ------------------------------------------------------------------ */

interface PricedResult {
  total: number;
  breakdown: QuoteLine[];
  loadFactorMultiplier: number;
}

function priceForClass(
  config: ConfigProvider,
  request: ParsedQuoteRequest,
  load: LoadProfile,
  vehicleClass: VehicleClass,
  assessment: FitAssessment,
  tripsRequired: number,
): PricedResult {
  const pricing = config.pricing();
  const rates = findClassPricing(config, vehicleClass.code);
  if (!rates) throw new PricingError('missing_class_pricing', `No pricing configured for "${vehicleClass.code}"`);

  const breakdown: QuoteLine[] = [];

  // --- Core: base + distance + time -------------------------------------
  const baseFare = rates.baseFare * tripsRequired;
  breakdown.push(line('base_fare', tripsRequired > 1 ? `Base fare x${tripsRequired} trips` : 'Base fare', baseFare));

  const distance = priceDistance(rates.distanceBands, request.route.distanceKm);
  const distanceAmount = distance.total * tripsRequired;
  breakdown.push(line('distance', `Distance (${format(request.route.distanceKm)} km)`, distanceAmount));

  const timeAmount = request.route.durationInTrafficMin * rates.timeRatePerMin * tripsRequired;
  breakdown.push(line('time', `Est. time in traffic (${Math.round(request.route.durationInTrafficMin)} min)`, timeAmount));

  const core = baseFare + distanceAmount + timeAmount;

  // --- Load factor ------------------------------------------------------
  const loadFactorMultiplier = loadFactorFor(config, assessment);
  const loadFactorAmount = core * (loadFactorMultiplier - 1);
  breakdown.push(
    line(
      'load_factor',
      assessment.weightFraction > assessment.volumeFraction
        ? `Load factor (heavy for its size, ${percent(assessment.weightFraction)} of payload)`
        : `Load factor (${percent(assessment.volumeFraction)} full)`,
      loadFactorAmount,
    ),
  );

  const subtotal = core + loadFactorAmount;

  // --- Surcharges (all disclosed before booking, itemised on the receipt) --
  if (isNightTrip(config, request)) {
    breakdown.push(line('night', 'Night surcharge (22:00-05:00)', subtotal * pricing.surcharges.night.rate));
  }

  // Uncapped surge is a PR grenade. The cap is config, and it is not optional.
  const cappedDemand = Math.min(request.demandMultiplier, pricing.surcharges.demandMultiplierCap);
  if (cappedDemand > 1) {
    breakdown.push(line('demand', `High demand (x${round(cappedDemand, 2)})`, subtotal * (cappedDemand - 1)));
  }

  if (request.returnLegRequired) {
    breakdown.push(line('return_leg', 'Return leg (driver waits and returns)', subtotal * pricing.surcharges.returnLegRate));
  }

  if (request.route.additionalStops > 0) {
    breakdown.push(
      line('stops', `Extra stops (${request.route.additionalStops})`, pricing.surcharges.additionalStopFee * request.route.additionalStops),
    );
  }

  if (load.fragile) breakdown.push(line('fragile', 'Fragile handling', pricing.surcharges.fragileHandlingFee));
  if (load.requiresEnclosed) breakdown.push(line('enclosed', 'Enclosed vehicle required', pricing.surcharges.enclosedVehicleFee));

  const helperAmount = priceHelpers(config, request);
  if (helperAmount > 0) {
    const floors = request.pickupFloors + request.dropoffFloors;
    breakdown.push(
      line('helpers', `Helper x${request.helpers}${floors > 0 ? ` (${floors} floor${floors === 1 ? '' : 's'})` : ''}`, helperAmount),
    );
  }

  // The promise, priced honestly at zero and shown as a line so nobody wonders.
  breakdown.push(line('curbside', `Curbside loading & unloading (${vehicleClass.freeHandlingMinutes} min incl.)`, 0));

  const gross = breakdown.reduce((sum, item) => sum + item.amount, 0);

  // --- Discounts --------------------------------------------------------
  for (const discount of computeDiscounts(config, request, gross)) breakdown.push(discount);

  const net = breakdown.reduce((sum, item) => sum + item.amount, 0);
  const floorPrice = rates.minimumFare * tripsRequired;
  const total = roundToNearest(Math.max(net, floorPrice), pricing.roundTotalToNearest);

  return { total, breakdown: breakdown.map((l) => ({ ...l, amount: round(l.amount, 0) })), loadFactorMultiplier };
}

/* ------------------------------------------------------------------ */
/* Components                                                          */
/* ------------------------------------------------------------------ */

/**
 * Volume bands, unless mass is the binding constraint — a canter carrying two
 * tonnes of cement in a fifth of its bed is fully committed, and pricing it on
 * empty space would pay the driver for a job they cannot double up on.
 */
export function loadFactorFor(config: ConfigProvider, assessment: FitAssessment): number {
  const { bands, densityCriticalMultiplier } = config.pricing().loadFactor;
  // Banded on whichever constraint commits the vehicle, not on space alone.
  const fraction = assessment.bindingFraction;

  let multiplier = bands[bands.length - 1]!.multiplier;
  for (const band of bands) {
    if (fraction <= band.upToFraction) {
      multiplier = band.multiplier;
      break;
    }
  }

  return assessment.densityCritical ? Math.max(multiplier, densityCriticalMultiplier) : multiplier;
}

/** Below the poolable threshold the driver may accept a shared second job. */
export function isPoolable(config: ConfigProvider, assessment: FitAssessment): boolean {
  return !assessment.densityCritical && assessment.bindingFraction <= config.pricing().loadFactor.poolableUpToFraction;
}

export function isNightTrip(config: ConfigProvider, request: ParsedQuoteRequest): boolean {
  if (!request.scheduledFor) return false;
  const { startHour, endHour } = config.pricing().surcharges.night;
  const hour = request.scheduledFor.getHours();
  // The window wraps midnight, so it is a union, not a range.
  return startHour > endHour ? hour >= startHour || hour < endHour : hour >= startHour && hour < endHour;
}

/**
 * §5 — a helper is paid labour, not a favour. Ground-level jobs still pay the
 * call-out, because the helper still showed up.
 */
export function priceHelpers(config: ConfigProvider, request: ParsedQuoteRequest): number {
  if (request.helpers <= 0) return 0;
  const { baseFeePerHelper, feePerFloorPerHelper, maxHelpers } = config.pricing().helpers;
  const helpers = Math.min(request.helpers, maxHelpers);
  const floors = request.pickupFloors + request.dropoffFloors;
  return helpers * (baseFeePerHelper + floors * feePerFloorPerHelper);
}

function computeDiscounts(config: ConfigProvider, request: ParsedQuoteRequest, gross: number): QuoteLine[] {
  const { backhaulRate, scheduledRate, businessTierRates, maxTotalRate } = config.pricing().discounts;

  const candidates: { code: string; label: string; rate: number }[] = [];
  // Empty return legs are ~40% of trucking cost. Filling one is the real margin.
  if (request.backhaulMatched) candidates.push({ code: 'backhaul', label: 'Return-load match', rate: backhaulRate });
  if (request.isScheduled) candidates.push({ code: 'scheduled', label: 'Scheduled booking', rate: scheduledRate });

  const tierRate = businessTierRates[request.businessTier] ?? 0;
  if (tierRate > 0) candidates.push({ code: 'business_tier', label: `Business account (${request.businessTier})`, rate: tierRate });

  const totalRate = candidates.reduce((sum, c) => sum + c.rate, 0);
  if (totalRate === 0) return [];

  // Stacked promos must never price below cost; scale them down together so no
  // single discount silently disappears from the receipt.
  const scale = totalRate > maxTotalRate ? maxTotalRate / totalRate : 1;

  return candidates.map((c) => line(`discount_${c.code}`, c.label, -(gross * c.rate * scale)));
}

/* ------------------------------------------------------------------ */
/* Anti-gaming (§4)                                                    */
/* ------------------------------------------------------------------ */

export interface CustomerRiskOutcome {
  toleranceFraction: number;
  requiresPhotoEstimate: boolean;
  requiresPrepay: boolean;
}

/**
 * A customer who habitually under-declares loses the tolerance band, must show
 * the system a photo, and prepays. Never punitive on a thin record — a first-time
 * customer with one bad trip is not a fraudster.
 */
export function evaluateCustomerRisk(
  config: ConfigProvider,
  risk: { underDeclarationRate: number; observations: number } | undefined,
): CustomerRiskOutcome {
  const { bandFraction, customerUnderDeclarationThreshold, minObservationsForScore } = config.pricing().tolerance;
  const clean: CustomerRiskOutcome = { toleranceFraction: bandFraction, requiresPhotoEstimate: false, requiresPrepay: false };

  if (!risk) return clean;
  if (risk.observations < minObservationsForScore) return clean;
  if (risk.underDeclarationRate <= customerUnderDeclarationThreshold) return clean;

  return { toleranceFraction: 0, requiresPhotoEstimate: true, requiresPrepay: true };
}

/* ------------------------------------------------------------------ */
/* Alternatives & inclusions                                           */
/* ------------------------------------------------------------------ */

function buildIncludedList(config: ConfigProvider, vehicleClass: VehicleClass): string[] {
  const pricing = config.pricing();
  return [
    `Curbside loading & unloading (${vehicleClass.freeHandlingMinutes} min included)`,
    `Goods cover up to ${pricing.currency} ${pricing.cover.includedValueCap.toLocaleString('en-KE')}`,
    'Live tracking you can share with the receiver',
  ];
}

/**
 * Always show a cheaper option with an honest warning (§6). Trust compounds; a
 * squeezed extra 200 shillings does not.
 */
function buildAlternatives(
  config: ConfigProvider,
  request: ParsedQuoteRequest,
  load: LoadProfile,
  chosen: VehicleClass,
  chosenTotal: number,
): QuoteAlternative[] {
  const alternatives: QuoteAlternative[] = [];
  const ordered = classesBySize(config, { onlyEnabled: true });

  // Cheaper: a smaller vehicle doing the job in several trips.
  for (const candidate of ordered.filter((c) => c.volumeM3 < chosen.volumeM3).reverse()) {
    const assessment = assessFit(config, candidate, load, { passengers: request.passengers });
    // Splitting solves space, never bed length, enclosure or seats.
    if (assessment.failures.some((f) => f !== 'volume' && f !== 'payload')) continue;

    const trips = Math.max(1, Math.ceil(assessment.volumeFraction), Math.ceil(assessment.weightFraction));
    const perTrip: FitAssessment = {
      ...assessment,
      fits: true,
      failures: [],
      volumeFraction: assessment.volumeFraction / trips,
      weightFraction: assessment.weightFraction / trips,
      bindingFraction: assessment.bindingFraction / trips,
    };
    const priced = priceForClass(config, request, load, candidate, perTrip, trips);
    if (priced.total >= chosenTotal) continue;

    alternatives.push({
      vehicleClass: candidate.code,
      total: priced.total,
      warning: trips > 1 ? `${trips} trips needed` : 'Tight fit — no room to spare',
    });
    break;
  }

  // Roomier: the next size up, for customers who would rather not risk it.
  const larger = ordered.find((c) => c.volumeM3 > chosen.volumeM3);
  if (larger) {
    const assessment = assessFit(config, larger, load, { passengers: request.passengers });
    if (assessment.fits) {
      const priced = priceForClass(config, request, load, larger, assessment, 1);
      alternatives.push({ vehicleClass: larger.code, total: priced.total, warning: 'More room than you need' });
    }
  }

  return alternatives;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function line(code: string, label: string, amount: number): QuoteLine {
  return { code, label, amount };
}

function roundToNearest(value: number, step: number): number {
  return Math.round(value / step) * step;
}

function round(n: number, dp: number): number {
  return Math.round(n * 10 ** dp) / 10 ** dp;
}

function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

function format(km: number): string {
  return String(Math.round(km * 10) / 10);
}

export type { ClassPricing };
