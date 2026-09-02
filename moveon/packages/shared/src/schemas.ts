import { z } from 'zod';

/**
 * move.on domain schemas.
 *
 * The single rule that shapes everything below: **cargo is measured in how much
 * of a vehicle it fills, never in kilograms the customer has to supply.**
 * Weight appears in this file only as a safety ceiling the *system* checks —
 * it is never an input we ask a human for.
 */

/* ------------------------------------------------------------------ */
/* Vehicle classes (§3)                                                */
/* ------------------------------------------------------------------ */

export const vehicleClassCodeSchema = z.string().min(1).max(40);
export type VehicleClassCode = z.infer<typeof vehicleClassCodeSchema>;

export const vehicleClassSchema = z.object({
  code: vehicleClassCodeSchema,
  displayName: z.string().min(1),
  /** Usable cargo envelope. This is the pricing container. */
  volumeM3: z.number().positive(),
  /** Legal/safety ceiling. A guardrail the system enforces — never a customer input. */
  payloadKg: z.number().positive(),
  /** Longest item the bed will take. Drives the `length_critical` check. */
  bedLengthM: z.number().positive(),
  /** Rain-safe / lockable. Required for `requires_enclosed` items. */
  enclosed: z.boolean(),
  /** Passengers who can ride along with their goods (§1). */
  passengerCapacity: z.number().int().min(0),
  /** Curbside handling included free, in minutes (§5). */
  freeHandlingMinutes: z.number().int().min(0),
  typicalJob: z.string(),
  /** §12: launch with two adjacent classes. Ops flips these on as supply grows. */
  enabled: z.boolean(),
});
export type VehicleClass = z.infer<typeof vehicleClassSchema>;

/* ------------------------------------------------------------------ */
/* Item catalog (§4 layer 1)                                           */
/* ------------------------------------------------------------------ */

export const catalogItemSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  category: z.string().min(1),
  /** Space the item occupies as handled — not its shrink-wrapped minimum. */
  volumeM3: z.number().positive(),
  weightKg: z.number().nonnegative(),
  fragile: z.boolean().default(false),
  requiresEnclosed: z.boolean().default(false),
  /** Discrete pieces a loader has to carry. Drives handling-time estimates. */
  handlingUnits: z.number().int().positive().default(1),
  /**
   * Small volume, huge mass — cement, blocks, tiles. Hits the payload ceiling
   * long before it fills the bed, so vehicle selection MUST check both.
   */
  densityCritical: z.boolean().default(false),
  /**
   * Iron sheets, timber, pipes. Fits nowhere by volume; needs a specific bed
   * length. Carries `minBedLengthM`.
   */
  lengthCritical: z.boolean().default(false),
  minBedLengthM: z.number().positive().optional(),
  /** Search synonyms in local usage — "mitumba", "gorogoro", "debe". */
  aliases: z.array(z.string()).default([]),
});
export type CatalogItem = z.infer<typeof catalogItemSchema>;

/** One-tap baskets: "1-bedroom house move", "salon relocation". */
export const catalogBundleSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  description: z.string(),
  items: z.array(z.object({ itemId: z.string().min(1), quantity: z.number().int().positive() })).min(1),
});
export type CatalogBundle = z.infer<typeof catalogBundleSchema>;

/* ------------------------------------------------------------------ */
/* Load declaration (§4)                                               */
/* ------------------------------------------------------------------ */

export const basketLineSchema = z.object({
  itemId: z.string().min(1),
  quantity: z.number().int().positive(),
});
export type BasketLine = z.infer<typeof basketLineSchema>;

/** Layer 2 — the fill-level slider, for loads the catalog can't name. */
export const fillLevelSchema = z.enum(['quarter', 'half', 'three_quarter', 'full', 'overflowing']);
export type FillLevel = z.infer<typeof fillLevelSchema>;

export const loadDeclarationSchema = z.discriminatedUnion('method', [
  z.object({
    method: z.literal('catalog'),
    lines: z.array(basketLineSchema).min(1),
  }),
  z.object({
    method: z.literal('fill_level'),
    referenceClass: vehicleClassCodeSchema,
    fillLevel: fillLevelSchema,
  }),
  z.object({
    method: z.literal('photo'),
    /** Confirmed by the customer — a photo estimate is never a silent decision. */
    estimatedVolumeM3: z.number().positive(),
    estimatedWeightKg: z.number().nonnegative(),
    confidence: z.number().min(0).max(1),
    photoIds: z.array(z.string()).min(1),
    requiresEnclosed: z.boolean().default(false),
    fragile: z.boolean().default(false),
  }),
]);
export type LoadDeclaration = z.infer<typeof loadDeclarationSchema>;

/** What every estimation layer normalises down to before pricing. */
export const loadProfileSchema = z.object({
  volumeM3: z.number().nonnegative(),
  weightKg: z.number().nonnegative(),
  fragile: z.boolean(),
  requiresEnclosed: z.boolean(),
  handlingUnits: z.number().int().nonnegative(),
  densityCritical: z.boolean(),
  minBedLengthM: z.number().nonnegative(),
  /** Human-readable reason we recommended what we recommended. */
  summary: z.string(),
});
export type LoadProfile = z.infer<typeof loadProfileSchema>;

/* ------------------------------------------------------------------ */
/* Quote request / response (§6)                                       */
/* ------------------------------------------------------------------ */

export const tripLegSchema = z.object({
  /** Straight billing distance for this leg, from the routing provider. */
  distanceKm: z.number().nonnegative(),
  /** duration_in_traffic. Half the driver's cost lives here (§2.4). */
  durationInTrafficMin: z.number().nonnegative(),
});
export type TripLeg = z.infer<typeof tripLegSchema>;

export const quoteRequestSchema = z.object({
  route: z.object({
    distanceKm: z.number().nonnegative(),
    durationInTrafficMin: z.number().nonnegative(),
    /** Stops beyond pickup + final dropoff. */
    additionalStops: z.number().int().min(0).default(0),
  }),
  load: loadDeclarationSchema,
  /**
   * The customer asking for a closed vehicle. Most goods here travel fine on an
   * open bed under a tarpaulin, so this is a preference the customer expresses,
   * not something the catalog decides for them — only genuinely weather-critical
   * items (a retail display chiller) force it on their own.
   */
  requiresEnclosed: z.boolean().default(false),
  /** Customer riding along with the goods. */
  passengers: z.number().int().min(0).default(0),
  /** Stairs at either end. Drives the helper add-on (§5). */
  pickupFloors: z.number().int().min(0).default(0),
  dropoffFloors: z.number().int().min(0).default(0),
  helpers: z.number().int().min(0).max(2).default(0),
  /** Local booking time. Drives the night surcharge. */
  scheduledFor: z.coerce.date().optional(),
  /** Non-instant booking — cheaper, because it lets us plan capacity. */
  isScheduled: z.boolean().default(false),
  /** Driver waits at the destination and brings the customer back. */
  returnLegRequired: z.boolean().default(false),
  /** Live demand multiplier from dispatch. Capped by config — uncapped surge is a PR grenade. */
  demandMultiplier: z.number().min(1).default(1),
  /** A matched empty return leg. The real margin (§6). */
  backhaulMatched: z.boolean().default(false),
  businessTier: z.enum(['none', 'bronze', 'silver', 'gold']).default('none'),
  /** Ops can pin a class; otherwise the engine recommends one. */
  forceVehicleClass: vehicleClassCodeSchema.optional(),
});
export type QuoteRequest = z.input<typeof quoteRequestSchema>;
export type ParsedQuoteRequest = z.infer<typeof quoteRequestSchema>;

export const quoteLineSchema = z.object({
  code: z.string(),
  label: z.string(),
  amount: z.number(),
});
export type QuoteLine = z.infer<typeof quoteLineSchema>;

export const quoteAlternativeSchema = z.object({
  vehicleClass: vehicleClassCodeSchema,
  total: z.number(),
  warning: z.string().optional(),
});
export type QuoteAlternative = z.infer<typeof quoteAlternativeSchema>;

export const quoteSchema = z.object({
  quoteId: z.string(),
  expiresAt: z.string(),
  vehicleClass: vehicleClassCodeSchema,
  recommendedReason: z.string(),
  total: z.number(),
  currency: z.string(),
  breakdown: z.array(quoteLineSchema),
  included: z.array(z.string()),
  alternatives: z.array(quoteAlternativeSchema),
  /** Everything downstream (dispatch, tolerance checks) needs this. Not shown to the customer. */
  meta: z.object({
    volumeM3: z.number(),
    weightKg: z.number(),
    fillFraction: z.number(),
    weightFraction: z.number(),
    densityCritical: z.boolean(),
    loadFactorMultiplier: z.number(),
    freeHandlingMinutes: z.number(),
    toleranceFraction: z.number(),
    requiresPhotoEstimate: z.boolean(),
    requiresPrepay: z.boolean(),
  }),
});
export type Quote = z.infer<typeof quoteSchema>;
