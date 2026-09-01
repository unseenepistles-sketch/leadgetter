import type { ConfigProvider, VehicleClass } from '@moveon/shared';
import { findClassPricing, findVehicleClass } from '@moveon/shared';
import { UnknownVehicleClassError, PricingError } from './errors.js';

/**
 * Loading & unloading policy (§5).
 *
 * The brief's original ask — "loading is free" — is wrong, and this module is
 * the honest version of it. Nothing is free; unpriced labour is paid for in
 * driver churn. A driver who spends 40 unpaid minutes hauling a fridge up four
 * flights earns less per hour than on a competing app, and leaves.
 *
 * So: curbside handling is genuinely included and the countdown is visible to
 * both parties. Beyond the window the meter runs, openly, per minute. Stairs are
 * a paid helper add-on. The claim we can defend is
 * "No hidden loading fees. Curbside handling included on every trip."
 */

export interface HandlingWindow {
  freeMinutes: number;
  graceMinutes: number;
  /** What both apps count down from, on screen, from the moment of arrival. */
  meterStartsAfterMinutes: number;
}

export function handlingWindow(config: ConfigProvider, vehicleClassCode: string): HandlingWindow {
  const vehicleClass: VehicleClass | undefined = findVehicleClass(config, vehicleClassCode);
  if (!vehicleClass) throw new UnknownVehicleClassError(vehicleClassCode);
  const graceMinutes = config.pricing().handling.graceMinutes;
  return {
    freeMinutes: vehicleClass.freeHandlingMinutes,
    graceMinutes,
    meterStartsAfterMinutes: vehicleClass.freeHandlingMinutes + graceMinutes,
  };
}

export interface WaitingCharge {
  chargeableMinutes: number;
  amount: number;
  waived: boolean;
  explanation: string;
}

/**
 * Metered waiting, ticking visibly. No surprises, no arguments — and the first
 * couple of overruns a month are absorbed as goodwill, because a customer whose
 * lift was broken is not a problem customer.
 */
export function priceWaiting(
  config: ConfigProvider,
  vehicleClassCode: string,
  elapsedMinutesOnSite: number,
  overrunsAlreadyThisMonth: number,
): WaitingCharge {
  const window = handlingWindow(config, vehicleClassCode);
  const rates = findClassPricing(config, vehicleClassCode);
  if (!rates) throw new PricingError('missing_class_pricing', `No pricing configured for "${vehicleClassCode}"`);

  const overrun = elapsedMinutesOnSite - window.meterStartsAfterMinutes;
  if (overrun <= 0) {
    return {
      chargeableMinutes: 0,
      amount: 0,
      waived: false,
      explanation: `Within the ${window.freeMinutes} min of included curbside handling`,
    };
  }

  const chargeableMinutes = Math.ceil(overrun);
  const { waivedOverrunsPerMonth } = config.pricing().handling;

  if (overrunsAlreadyThisMonth < waivedOverrunsPerMonth) {
    return {
      chargeableMinutes,
      amount: 0,
      waived: true,
      explanation: `Waiting time waived (${overrunsAlreadyThisMonth + 1} of ${waivedOverrunsPerMonth} this month)`,
    };
  }

  return {
    chargeableMinutes,
    amount: Math.round(chargeableMinutes * rates.waitingRatePerMin),
    waived: false,
    explanation: `${chargeableMinutes} min beyond the included ${window.freeMinutes} min`,
  };
}

/**
 * Stairs discovered at pickup become an in-app upsell the customer taps to
 * approve — never a negotiation on the pavement.
 */
export function priceHelperUpsell(
  config: ConfigProvider,
  helpers: number,
  floors: number,
): { amount: number; helpers: number; explanation: string } {
  const { baseFeePerHelper, feePerFloorPerHelper, maxHelpers } = config.pricing().helpers;
  const capped = Math.max(0, Math.min(helpers, maxHelpers));
  const amount = capped * (baseFeePerHelper + Math.max(0, floors) * feePerFloorPerHelper);
  return {
    amount,
    helpers: capped,
    explanation: capped === 0
      ? 'No helpers requested'
      : `${capped} helper${capped === 1 ? '' : 's'} for ${floors} floor${floors === 1 ? '' : 's'}`,
  };
}
