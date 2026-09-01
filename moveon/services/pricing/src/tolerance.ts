import type { ConfigProvider, LoadProfile, ParsedQuoteRequest, Quote } from '@moveon/shared';
import { findVehicleClass } from '@moveon/shared';
import { assessFit, recommendVehicle } from './vehicle.js';
import { UnknownVehicleClassError } from './errors.js';
import { priceQuote, type QuoteContext } from './quote.js';

/**
 * Driver confirmation with a tolerance band (§4, layer 4).
 *
 * At pickup the driver taps one of three outcomes. The middle one — slightly
 * bigger than declared — is absorbed silently and deliberately. That tolerance
 * costs a little margin per trip and buys peace at scale: two humans never
 * negotiate money at the roadside, which is the failure this whole product
 * exists to prevent.
 *
 * Only a load that genuinely will not fit the dispatched vehicle triggers a
 * re-quote, and the re-quote happens in the app, with the customer approving or
 * cancelling. If they cancel, the driver is paid a dead-run fee by the platform,
 * not by an argument.
 */

export type LoadConfirmationOutcome = 'matches' | 'absorbed' | 'requote_required';

export interface ActualLoad {
  volumeM3: number;
  weightKg: number;
  minBedLengthM?: number;
  requiresEnclosed?: boolean;
}

export interface LoadConfirmationResult {
  outcome: LoadConfirmationOutcome;
  /** Positive means the load is bigger than declared. */
  overrunFraction: number;
  /** Counts against the customer even when the trip proceeds unchanged. */
  underDeclared: boolean;
  suggestedVehicleClass?: string;
  message: string;
  /** True when the driver's own record means this re-quote needs a human or a photo. */
  requiresOpsApproval: boolean;
}

export interface DriverRisk {
  overReportRate: number;
  observations: number;
}

export function confirmLoad(
  config: ConfigProvider,
  quote: Quote,
  actual: ActualLoad,
  options: { passengers?: number; driverRisk?: DriverRisk } = {},
): LoadConfirmationResult {
  const dispatched = findVehicleClass(config, quote.vehicleClass);
  if (!dispatched) throw new UnknownVehicleClassError(quote.vehicleClass);

  const declaredVolume = quote.meta.volumeM3;
  const overrunFraction = declaredVolume > 0 ? actual.volumeM3 / declaredVolume - 1 : 0;

  const actualProfile: LoadProfile = {
    volumeM3: actual.volumeM3,
    weightKg: actual.weightKg,
    fragile: false,
    requiresEnclosed: actual.requiresEnclosed ?? false,
    handlingUnits: 1,
    densityCritical: false,
    minBedLengthM: actual.minBedLengthM ?? 0,
    summary: `Confirmed at pickup: ~${Math.round(actual.volumeM3 * 10) / 10} m³`,
  };

  const passengers = options.passengers ?? 0;
  const fit = assessFit(config, dispatched, actualProfile, { passengers });
  const tolerance = quote.meta.toleranceFraction;
  const underDeclared = overrunFraction > tolerance;

  // The vehicle we sent can still do the job.
  if (fit.fits) {
    if (overrunFraction <= 0) {
      return {
        outcome: 'matches',
        overrunFraction: roundFraction(overrunFraction),
        underDeclared: false,
        message: 'Load matches the booking. Go.',
        requiresOpsApproval: false,
      };
    }
    return {
      outcome: 'absorbed',
      overrunFraction: roundFraction(overrunFraction),
      // Logged either way — the tolerance is a courtesy, not a blind spot.
      underDeclared,
      message: underDeclared
        ? 'Bigger than booked, but it fits. Proceeding at the quoted price.'
        : 'Slightly bigger than booked. Within tolerance — no change to your price.',
      requiresOpsApproval: false,
    };
  }

  // It does not fit. Find what does, and re-quote in-app.
  const replacement = safeRecommend(config, actualProfile, passengers);
  const driverRisk = options.driverRisk;
  const { driverOverReportThreshold, minObservationsForScore } = config.pricing().tolerance;
  const requiresOpsApproval =
    driverRisk !== undefined &&
    driverRisk.observations >= minObservationsForScore &&
    driverRisk.overReportRate > driverOverReportThreshold;

  return {
    outcome: 'requote_required',
    overrunFraction: roundFraction(overrunFraction),
    underDeclared: true,
    suggestedVehicleClass: replacement,
    message: replacement
      ? `This load needs a bigger vehicle. Sending the customer a new price for a ${replacement}.`
      : 'This load exceeds every available vehicle. Ops will call the customer.',
    requiresOpsApproval,
  };
}

function safeRecommend(config: ConfigProvider, profile: LoadProfile, passengers: number): string | undefined {
  try {
    const recommendation = recommendVehicle(config, profile, { passengers });
    // Splitting a load across several trips is a decision made at booking, with
    // the customer choosing it. Proposing it to a driver already standing at the
    // pickup is not a re-quote, it is an argument. Ops takes those by phone.
    if (recommendation.tripsRequired > 1) return undefined;
    return recommendation.assessment.vehicleClass.code;
  } catch {
    // Nothing available can take it at all.
    return undefined;
  }
}

export interface RequoteResult {
  original: Quote;
  revised: Quote;
  difference: number;
  /** Paid by the platform to the driver if the customer walks. Never argued over. */
  deadRunFeeIfCancelled: number;
}

/**
 * Builds the replacement quote the customer approves or declines in-app.
 * Pure, like the engine it wraps: the caller supplies the clock and the id.
 */
export function buildRequote(
  config: ConfigProvider,
  originalQuote: Quote,
  originalRequest: ParsedQuoteRequest,
  actual: ActualLoad,
  context: QuoteContext,
): RequoteResult {
  const revised = priceQuote(
    config,
    {
      ...originalRequest,
      load: {
        method: 'photo',
        estimatedVolumeM3: actual.volumeM3,
        estimatedWeightKg: actual.weightKg,
        confidence: 1,
        photoIds: ['driver-confirmation'],
        requiresEnclosed: actual.requiresEnclosed ?? false,
        fragile: false,
      },
    },
    context,
  );

  const rates = config.classPricing().find((p) => p.vehicleClass === originalQuote.vehicleClass);
  return {
    original: originalQuote,
    revised,
    difference: revised.total - originalQuote.total,
    deadRunFeeIfCancelled: rates ? rates.deadRunFee : 0,
  };
}

/* ------------------------------------------------------------------ */
/* Reputation scoring                                                  */
/* ------------------------------------------------------------------ */

/**
 * Never let one side's word be final (§4). Both rates are computed the same way
 * and both gate the same way — an under-declaring customer loses their tolerance
 * band; an over-reporting driver loses the right to re-quote unsupervised.
 */
export function updateRate(previous: { rate: number; observations: number }, incident: boolean): { rate: number; observations: number } {
  const observations = previous.observations + 1;
  const incidents = previous.rate * previous.observations + (incident ? 1 : 0);
  return { rate: roundFraction(incidents / observations), observations };
}

export function customerLosesTolerance(config: ConfigProvider, risk: { rate: number; observations: number }): boolean {
  const { customerUnderDeclarationThreshold, minObservationsForScore } = config.pricing().tolerance;
  return risk.observations >= minObservationsForScore && risk.rate > customerUnderDeclarationThreshold;
}

export function driverNeedsApproval(config: ConfigProvider, risk: { rate: number; observations: number }): boolean {
  const { driverOverReportThreshold, minObservationsForScore } = config.pricing().tolerance;
  return risk.observations >= minObservationsForScore && risk.rate > driverOverReportThreshold;
}

function roundFraction(n: number): number {
  return Math.round(n * 10000) / 10000;
}
