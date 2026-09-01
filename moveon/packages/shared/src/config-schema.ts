import { z } from 'zod';
import { vehicleClassCodeSchema } from './schemas.js';

/**
 * Everything a pricing decision depends on lives here, and everything here is
 * data. Ops edits rates without a deploy (§3); the engine reads whatever the
 * ConfigProvider hands it. No number in the pricing service is a literal.
 */

/** A tapered distance band. `uptoKm: null` is the open-ended intercity tier. */
export const distanceBandSchema = z.object({
  uptoKm: z.number().positive().nullable(),
  ratePerKm: z.number().nonnegative(),
  label: z.string(),
});
export type DistanceBand = z.infer<typeof distanceBandSchema>;

export const classPricingSchema = z.object({
  vehicleClass: vehicleClassCodeSchema,
  baseFare: z.number().nonnegative(),
  minimumFare: z.number().nonnegative(),
  /** Marginal, tax-bracket style. Flat per-km makes us 3x the market on long hauls. */
  distanceBands: z.array(distanceBandSchema).min(1),
  /** Price the driver's hours, not just their odometer. */
  timeRatePerMin: z.number().nonnegative(),
  /** Ticks visibly, after the free curbside window is spent. */
  waitingRatePerMin: z.number().nonnegative(),
  /** Paid to the driver when a customer cancels after an honest re-quote. */
  deadRunFee: z.number().nonnegative(),
});
export type ClassPricing = z.infer<typeof classPricingSchema>;

export const pricingConfigSchema = z.object({
  currency: z.string().min(3),
  /** Round the customer-facing total to something a person can hand over in cash. */
  roundTotalToNearest: z.number().positive(),
  quoteTtlSeconds: z.number().int().positive(),

  loadFactor: z.object({
    /** fillFraction <= this -> multiplier. Driver may pool a second job below the first band. */
    bands: z.array(z.object({ upToFraction: z.number().positive(), multiplier: z.number().min(1) })).min(1),
    /** Payload ceiling reached before volume — cement, blocks, tiles. */
    densityCriticalMultiplier: z.number().min(1),
    /** Weight utilisation at or above which a load counts as density-critical. */
    densityCriticalThreshold: z.number().min(0).max(1),
    /** At or below this fill, the load is poolable and pays no exclusivity premium. */
    poolableUpToFraction: z.number().min(0).max(1),
  }),

  estimation: z.object({
    /**
     * Fill-slider and photo loads have no per-item weights. We assume a
     * household-goods density; the payload ceiling still guards the vehicle.
     */
    nominalDensityKgPerM3: z.number().positive(),
    /** How much of the reference vehicle each slider notch means. */
    fillLevelFractions: z.object({
      quarter: z.number().positive(),
      half: z.number().positive(),
      three_quarter: z.number().positive(),
      full: z.number().positive(),
      overflowing: z.number().positive(),
    }),
    /** Below this, a photo estimate is too weak to lead with — fall back to the catalog. */
    photoMinConfidence: z.number().min(0).max(1),
    /** Pieces per cubic metre, for estimating how long handling will take. */
    handlingUnitsPerM3: z.number().positive(),
  }),

  surcharges: z.object({
    night: z.object({ startHour: z.number().int().min(0).max(23), endHour: z.number().int().min(0).max(23), rate: z.number().min(0) }),
    /** Hard cap. Uncapped surge is a PR grenade (§6). */
    demandMultiplierCap: z.number().min(1),
    returnLegRate: z.number().min(0),
    additionalStopFee: z.number().nonnegative(),
    fragileHandlingFee: z.number().nonnegative(),
    enclosedVehicleFee: z.number().nonnegative(),
  }),

  helpers: z.object({
    /** A helper booked for a ground-level job still costs something — they showed up. */
    baseFeePerHelper: z.number().nonnegative(),
    feePerFloorPerHelper: z.number().nonnegative(),
    maxHelpers: z.number().int().positive(),
  }),

  discounts: z.object({
    /** Empty return legs are ~40% of trucking cost. This is the real margin. */
    backhaulRate: z.number().min(0).max(1),
    scheduledRate: z.number().min(0).max(1),
    businessTierRates: z.record(z.string(), z.number().min(0).max(1)),
    /** Stacked discounts must never price below cost. */
    maxTotalRate: z.number().min(0).max(1),
  }),

  handling: z.object({
    /** Overruns waived per customer per month — the goodwill budget (§5). */
    waivedOverrunsPerMonth: z.number().int().min(0),
    /** Grace before the meter starts, once the free window is spent. */
    graceMinutes: z.number().min(0),
  }),

  tolerance: z.object({
    /** Slightly bigger than declared is absorbed silently. This buys peace at scale (§4). */
    bandFraction: z.number().min(0).max(1),
    /** Above this under-declaration rate the customer loses the band and prepays. */
    customerUnderDeclarationThreshold: z.number().min(0).max(1),
    /** Above this the driver's re-quotes need ops approval or a photo. */
    driverOverReportThreshold: z.number().min(0).max(1),
    /** Minimum confirmed jobs before a rate is trusted enough to act on. */
    minObservationsForScore: z.number().int().positive(),
  }),

  cover: z.object({
    /** Free goods cover. Above this the customer declares value or loses cover (§8). */
    includedValueCap: z.number().nonnegative(),
  }),
});
export type PricingConfig = z.infer<typeof pricingConfigSchema>;
