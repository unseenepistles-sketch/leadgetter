import { describe, it, expect } from 'vitest';
import { defaultConfig, quoteRequestSchema } from '@moveon/shared';
import { priceQuote, loadFactorFor, isPoolable, isNightTrip, priceHelpers, evaluateCustomerRisk } from './quote.js';
import { assessFit } from './vehicle.js';
import { fromCatalogBasket } from './load.js';
import { UnknownVehicleClassError, PricingError, NoSuitableVehicleError } from './errors.js';
import { CTX, makeRequest, basket, configWith, configWithClasses, bareProvider, lineAmount } from './testing.js';
import { findVehicleClass } from '@moveon/shared';

const canter = findVehicleClass(defaultConfig, 'canter_3t')!;

describe('the quote object (§6)', () => {
  const quote = priceQuote(defaultConfig, makeRequest(), CTX);

  it('returns the documented shape', () => {
    expect(quote).toMatchObject({
      quoteId: 'q_test',
      currency: 'KES',
      vehicleClass: expect.any(String),
      recommendedReason: expect.any(String),
      total: expect.any(Number),
    });
    expect(quote.breakdown.length).toBeGreaterThan(0);
    expect(quote.included.length).toBeGreaterThan(0);
  });

  it('expires, so a stale price is never charged', () => {
    expect(quote.expiresAt).toBe(new Date(CTX.now.getTime() + 900_000).toISOString());
  });

  it('itemises everything the customer pays for', () => {
    const codes = quote.breakdown.map((l) => l.code);
    expect(codes).toContain('base_fare');
    expect(codes).toContain('distance');
    expect(codes).toContain('time');
    expect(codes).toContain('curbside');
  });

  it('states what is included, in the customer\'s words', () => {
    expect(quote.included[0]).toMatch(/Curbside loading & unloading/);
    expect(quote.included[1]).toMatch(/Goods cover up to KES 50,000/);
  });

  it('is pure — the same inputs always produce the same price', () => {
    const a = priceQuote(defaultConfig, makeRequest(), CTX);
    const b = priceQuote(defaultConfig, makeRequest(), CTX);
    expect(a).toEqual(b);
  });

  it('reproduces the brief\'s worked example within rounding', () => {
    const result = priceQuote(defaultConfig, makeRequest({
      route: { distanceKm: 23.4, durationInTrafficMin: 48 },
      load: basket(['sofa_5seater', 1], ['bed_frame_headboard', 2], ['wardrobe_2door', 2],
                   ['dining_set_6', 1], ['carton_medium', 15]),
      helpers: 1,
    }), CTX);
    expect(result.vehicleClass).toBe('canter_3t');
    expect(lineAmount(result.breakdown, 'base_fare')).toBe(800);
    expect(lineAmount(result.breakdown, 'distance')).toBe(2351);
    expect(lineAmount(result.breakdown, 'time')).toBe(960);
    expect(lineAmount(result.breakdown, 'curbside')).toBe(0);
  });
});

describe('time in traffic is priced, not just distance (§2.4)', () => {
  it('charges more for the same 6 km in rush hour than on an open road', () => {
    const route = (durationInTrafficMin: number) =>
      priceQuote(defaultConfig, makeRequest({ route: { distanceKm: 6, durationInTrafficMin } }), CTX).total;
    expect(route(40)).toBeGreaterThan(route(8));
  });

  it('without it, drivers would cherry-pick — so it is never zero-rated by default', () => {
    for (const row of defaultConfig.classPricing()) expect(row.timeRatePerMin).toBeGreaterThan(0);
  });
});

describe('load factor bands (§6)', () => {
  const fit = (volumeM3: number, weightKg = 0) =>
    assessFit(defaultConfig, canter, {
      volumeM3, weightKg, fragile: false, requiresEnclosed: false,
      handlingUnits: 1, densityCritical: false, minBedLengthM: 0, summary: '',
    }, { passengers: 0 });

  // Every band boundary, exactly on the line and a hair either side.
  it.each([
    { m3: 0, expected: 1.0 },
    { m3: 5.9999, expected: 1.0 },
    { m3: 6, expected: 1.0 },       // exactly 0.5 — inclusive lower band
    { m3: 6.01, expected: 1.08 },
    { m3: 10.2, expected: 1.08 },   // exactly 0.85
    { m3: 10.21, expected: 1.15 },
    { m3: 12, expected: 1.15 },     // exactly full
  ])('$m3 m³ in a canter prices at x$expected', ({ m3, expected }) => {
    expect(loadFactorFor(defaultConfig, fit(m3))).toBe(expected);
  });

  it('falls back to the top band for a load beyond the last boundary', () => {
    expect(loadFactorFor(defaultConfig, fit(13))).toBe(1.15);
  });

  it('applies the density premium when mass reaches the ceiling first', () => {
    expect(loadFactorFor(defaultConfig, fit(1, 2800))).toBe(1.2);
  });

  it('never lets the density premium undercut a volume band', () => {
    const config = configWith({ pricing: (p) => { p.loadFactor.densityCriticalMultiplier = 1.01; } });
    expect(loadFactorFor(config, fit(11.9, 2900))).toBe(1.15);
  });

  it('bands on mass when mass is what commits the vehicle', () => {
    // 12% full but two-thirds of the payload: the driver cannot pool this.
    expect(loadFactorFor(defaultConfig, fit(1.4, 2000))).toBe(1.08);
  });

  it('shows the customer the constraint they are actually paying for', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ load: basket(['sack_50kg_cement', 40]) }), CTX);
    expect(quote.breakdown.find((l) => l.code === 'load_factor')?.label)
      .toBe('Load factor (heavy for its size, 67% of payload)');
  });

  it('describes a bulky load by how full it is', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ load: basket(['mattress_double', 2]) }), CTX);
    expect(quote.breakdown.find((l) => l.code === 'load_factor')?.label).toMatch(/^Load factor \(\d+% full\)$/);
  });
});

describe('pooling', () => {
  const fit = (volumeM3: number, weightKg = 0) =>
    assessFit(defaultConfig, canter, {
      volumeM3, weightKg, fragile: false, requiresEnclosed: false,
      handlingUnits: 1, densityCritical: false, minBedLengthM: 0, summary: '',
    }, { passengers: 0 });

  it('lets a driver take a second job on a half-empty vehicle', () => {
    expect(isPoolable(defaultConfig, fit(5))).toBe(true);
  });

  it('does not, once the vehicle is committed', () => {
    expect(isPoolable(defaultConfig, fit(9))).toBe(false);
  });

  it('does not on a heavy load, however much space is left', () => {
    expect(isPoolable(defaultConfig, fit(1, 2800))).toBe(false);
  });
});

describe('surcharges — all disclosed before booking', () => {
  it('adds a night surcharge inside the window', () => {
    const night = makeRequest({ scheduledFor: new Date(2026, 2, 4, 23, 30) });
    expect(lineAmount(priceQuote(defaultConfig, night, CTX).breakdown, 'night')).toBeGreaterThan(0);
  });

  it('does not, in the afternoon', () => {
    const day = makeRequest({ scheduledFor: new Date(2026, 2, 4, 14, 0) });
    expect(lineAmount(priceQuote(defaultConfig, day, CTX).breakdown, 'night')).toBeUndefined();
  });

  it('does not, when no time was given', () => {
    expect(lineAmount(priceQuote(defaultConfig, makeRequest(), CTX).breakdown, 'night')).toBeUndefined();
  });

  describe('the night window wraps midnight', () => {
    const at = (hour: number) => isNightTrip(defaultConfig, makeRequest({ scheduledFor: new Date(2026, 2, 4, hour, 0) }));
    it.each([
      [21, false], [22, true], [23, true], [0, true], [4, true], [5, false], [12, false],
    ])('%i:00 is night = %s', (hour, expected) => expect(at(hour)).toBe(expected));
  });

  it('handles a same-day window that does not wrap', () => {
    const config = configWith({ pricing: (p) => { p.surcharges.night = { startHour: 10, endHour: 14, rate: 0.1 }; } });
    const at = (hour: number) => isNightTrip(config, makeRequest({ scheduledFor: new Date(2026, 2, 4, hour, 0) }));
    expect(at(9)).toBe(false);
    expect(at(10)).toBe(true);
    expect(at(13)).toBe(true);
    expect(at(14)).toBe(false);
  });

  it('caps surge — uncapped surge is a PR grenade', () => {
    const wild = priceQuote(defaultConfig, makeRequest({ demandMultiplier: 6 }), CTX);
    const capped = priceQuote(defaultConfig, makeRequest({ demandMultiplier: 1.8 }), CTX);
    expect(wild.total).toBe(capped.total);
    expect(wild.breakdown.find((l) => l.code === 'demand')?.label).toBe('High demand (x1.8)');
  });

  it('adds nothing at normal demand', () => {
    expect(lineAmount(priceQuote(defaultConfig, makeRequest(), CTX).breakdown, 'demand')).toBeUndefined();
  });

  it('charges for a driver who waits and comes back', () => {
    expect(lineAmount(priceQuote(defaultConfig, makeRequest({ returnLegRequired: true }), CTX).breakdown, 'return_leg'))
      .toBeGreaterThan(0);
  });

  it('charges a flat fee per extra stop', () => {
    const two = priceQuote(defaultConfig, makeRequest({ route: { distanceKm: 10, durationInTrafficMin: 20, additionalStops: 2 } }), CTX);
    expect(lineAmount(two.breakdown, 'stops')).toBe(600);
    expect(two.breakdown.find((l) => l.code === 'stops')?.label).toBe('Extra stops (2)');
  });

  it('charges nothing for a direct trip', () => {
    expect(lineAmount(priceQuote(defaultConfig, makeRequest(), CTX).breakdown, 'stops')).toBeUndefined();
  });

  it('charges for fragile handling when the basket contains something breakable', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ load: basket(['crate_sodas', 4]) }), CTX);
    expect(lineAmount(quote.breakdown, 'fragile')).toBe(250);
  });

  it('charges for an enclosed vehicle when the goods demand one', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ load: basket(['display_fridge', 1]), forceVehicleClass: 'panel_van' }), CTX);
    expect(lineAmount(quote.breakdown, 'enclosed')).toBe(400);
  });

  it('charges for one when the customer simply wants their load kept dry', () => {
    const quote = priceQuote(defaultConfig, makeRequest({
      load: basket(['carton_medium', 2]), requiresEnclosed: true, forceVehicleClass: 'panel_van',
    }), CTX);
    expect(lineAmount(quote.breakdown, 'enclosed')).toBe(400);
  });

  it('refuses when the customer wants a closed vehicle and none is running', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    expect(() => priceQuote(config, makeRequest({ load: basket(['carton_medium', 2]), requiresEnclosed: true }), CTX))
      .toThrow(/enclosed vehicle/);
  });

  it('charges neither for plain dry goods', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ load: basket(['sack_50kg_grain', 4]) }), CTX);
    expect(lineAmount(quote.breakdown, 'fragile')).toBeUndefined();
    expect(lineAmount(quote.breakdown, 'enclosed')).toBeUndefined();
  });
});

describe('helpers — paid labour, not a favour (§5)', () => {
  it('charges a call-out even at ground level, because the helper showed up', () => {
    expect(priceHelpers(defaultConfig, makeRequest({ helpers: 1 }))).toBe(300);
  });

  it('charges per floor per helper', () => {
    expect(priceHelpers(defaultConfig, makeRequest({ helpers: 2, pickupFloors: 3, dropoffFloors: 1 }))).toBe(2 * (300 + 4 * 150));
  });

  it('charges nothing when none were booked', () => {
    expect(priceHelpers(defaultConfig, makeRequest())).toBe(0);
    expect(lineAmount(priceQuote(defaultConfig, makeRequest(), CTX).breakdown, 'helpers')).toBeUndefined();
  });

  it('caps at the configured maximum', () => {
    const request = quoteRequestSchema.parse({ ...makeRequest(), helpers: 2 });
    const capped = configWith({ pricing: (p) => { p.helpers.maxHelpers = 1; } });
    expect(priceHelpers(capped, request)).toBe(300);
  });

  it('names the floors on the receipt', () => {
    const one = priceQuote(defaultConfig, makeRequest({ helpers: 1, pickupFloors: 1 }), CTX);
    expect(one.breakdown.find((l) => l.code === 'helpers')?.label).toBe('Helper x1 (1 floor)');
    const many = priceQuote(defaultConfig, makeRequest({ helpers: 1, pickupFloors: 2, dropoffFloors: 1 }), CTX);
    expect(many.breakdown.find((l) => l.code === 'helpers')?.label).toBe('Helper x1 (3 floors)');
    const ground = priceQuote(defaultConfig, makeRequest({ helpers: 1 }), CTX);
    expect(ground.breakdown.find((l) => l.code === 'helpers')?.label).toBe('Helper x1');
  });

  it('always shows curbside handling as included, at zero', () => {
    const quote = priceQuote(defaultConfig, makeRequest(), CTX);
    const line = quote.breakdown.find((l) => l.code === 'curbside')!;
    expect(line.amount).toBe(0);
    expect(line.label).toContain('min incl.');
  });
});

describe('discounts', () => {
  const gross = (request = makeRequest()) => priceQuote(defaultConfig, request, CTX).total;

  it('discounts a matched return load — the real margin', () => {
    const matched = priceQuote(defaultConfig, makeRequest({ backhaulMatched: true, route: { distanceKm: 120, durationInTrafficMin: 100 } }), CTX);
    expect(lineAmount(matched.breakdown, 'discount_backhaul')).toBeLessThan(0);
    expect(matched.total).toBeLessThan(gross(makeRequest({ route: { distanceKm: 120, durationInTrafficMin: 100 } })));
  });

  it('discounts a scheduled booking, because it lets us plan capacity', () => {
    const scheduled = priceQuote(defaultConfig, makeRequest({ isScheduled: true, route: { distanceKm: 60, durationInTrafficMin: 60 } }), CTX);
    expect(lineAmount(scheduled.breakdown, 'discount_scheduled')).toBeLessThan(0);
  });

  it('discounts by business tier', () => {
    const gold = priceQuote(defaultConfig, makeRequest({ businessTier: 'gold', route: { distanceKm: 60, durationInTrafficMin: 60 } }), CTX);
    expect(gold.breakdown.find((l) => l.code === 'discount_business_tier')?.label).toBe('Business account (gold)');
  });

  it('adds no discount lines for a plain retail booking', () => {
    expect(priceQuote(defaultConfig, makeRequest(), CTX).breakdown.filter((l) => l.code.startsWith('discount_'))).toEqual([]);
  });

  it('adds none for a business tier configured at zero', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ businessTier: 'none' }), CTX);
    expect(quote.breakdown.filter((l) => l.code.startsWith('discount_'))).toEqual([]);
  });

  it('scales stacked discounts down together rather than pricing below cost', () => {
    const request = makeRequest({
      backhaulMatched: true, isScheduled: true, businessTier: 'gold',
      route: { distanceKm: 150, durationInTrafficMin: 120 },
    });
    const quote = priceQuote(defaultConfig, request, CTX);
    const discountTotal = -quote.breakdown.filter((l) => l.code.startsWith('discount_')).reduce((s, l) => s + l.amount, 0);
    const chargeTotal = quote.breakdown.filter((l) => !l.code.startsWith('discount_')).reduce((s, l) => s + l.amount, 0);
    // 25 + 7 + 10 = 42% requested, capped at 35%.
    expect(discountTotal / chargeTotal).toBeCloseTo(0.35, 2);
    expect(quote.breakdown.filter((l) => l.code.startsWith('discount_'))).toHaveLength(3);
  });

  it('ignores an unknown business tier rather than crashing on it', () => {
    const odd = configWith({ pricing: (p) => { delete p.discounts.businessTierRates.gold; } });
    const quote = priceQuote(odd, makeRequest({ businessTier: 'gold' }), CTX);
    expect(quote.breakdown.filter((l) => l.code.startsWith('discount_'))).toEqual([]);
  });

  it('never discounts below the class minimum fare', () => {
    const quote = priceQuote(defaultConfig, makeRequest({
      route: { distanceKm: 1, durationInTrafficMin: 2 },
      backhaulMatched: true, isScheduled: true, businessTier: 'gold',
    }), CTX);
    expect(quote.total).toBe(600);
  });
});

describe('minimum fare', () => {
  it('floors a very short trip', () => {
    expect(priceQuote(defaultConfig, makeRequest({ route: { distanceKm: 0.2, durationInTrafficMin: 1 } }), CTX).total).toBe(600);
  });

  it('scales the floor with the number of trips', () => {
    const config = configWithClasses('pickup_single');
    const quote = priceQuote(config, makeRequest({
      route: { distanceKm: 0.2, durationInTrafficMin: 1 },
      load: basket(['mattress_double', 12]),
    }), CTX);
    expect(quote.total).toBe(600 * 3);
  });

  it('rounds to something a person can hand over in cash', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ route: { distanceKm: 17.3, durationInTrafficMin: 33 } }), CTX);
    expect(quote.total % 10).toBe(0);
  });
});

describe('multi-trip pricing', () => {
  it('labels the repeated base fare honestly', () => {
    const config = configWithClasses('pickup_single');
    const quote = priceQuote(config, makeRequest({ load: basket(['mattress_double', 12]) }), CTX);
    expect(quote.breakdown.find((l) => l.code === 'base_fare')?.label).toBe('Base fare x3 trips');
    expect(quote.recommendedReason).toContain('across 3 trips');
  });
});

describe('forced vehicle class', () => {
  it('prices what ops asked for', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ forceVehicleClass: 'lorry_7t' }), CTX);
    expect(quote.vehicleClass).toBe('lorry_7t');
    expect(quote.recommendedReason).toContain('Lorry 7t selected');
  });

  it('rejects a class that is not configured', () => {
    expect(() => priceQuote(defaultConfig, makeRequest({ forceVehicleClass: 'zeppelin' }), CTX))
      .toThrow(UnknownVehicleClassError);
  });

  it('fails loudly when a class has no rate card', () => {
    const config = bareProvider({ classPricing: defaultConfig.classPricing().filter((p) => p.vehicleClass !== 'canter_3t') });
    expect(() => priceQuote(config, makeRequest({ forceVehicleClass: 'canter_3t' }), CTX))
      .toThrow(PricingError);
  });

  it('surfaces an impossible load as a typed refusal', () => {
    const config = configWithClasses('pickup_single');
    expect(() => priceQuote(config, makeRequest({ load: basket(['ppr_pipes_10', 1]) }), CTX))
      .toThrow(NoSuitableVehicleError);
  });
});

describe('alternatives — always an honest cheaper option (§6)', () => {
  it('offers a smaller vehicle doing several trips, where that is genuinely cheaper', () => {
    // Canters are scarce in this market and priced accordingly, so two pickup
    // runs beat one canter run. Ops changes that by editing the rate card.
    const scarceCanters = configWith({
      vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = ['pickup_single', 'canter_3t'].includes(c.code); }),
      classPricing: (rows) => {
        const canterRates = rows.find((r) => r.vehicleClass === 'canter_3t')!;
        canterRates.baseFare = 2600;
        canterRates.minimumFare = 2600;
      },
    });
    const quote = priceQuote(scarceCanters, makeRequest({
      route: { distanceKm: 4, durationInTrafficMin: 10 },
      load: basket(['mattress_double', 6]),
    }), CTX);
    expect(quote.vehicleClass).toBe('canter_3t');
    const cheaper = quote.alternatives.find((a) => a.vehicleClass === 'pickup_single');
    expect(cheaper!.total).toBeLessThan(quote.total);
    expect(cheaper!.warning).toBe('2 trips needed');
  });

  it('offers no split alternative when two small trips cost more than one big one', () => {
    // On the shipped rate card a canter is well under twice a pickup, so
    // splitting is a worse deal and we do not pretend otherwise.
    const config = configWithClasses('pickup_single', 'canter_3t');
    const quote = priceQuote(config, makeRequest({
      route: { distanceKm: 4, durationInTrafficMin: 10 },
      load: basket(['mattress_double', 6]),
    }), CTX);
    expect(quote.vehicleClass).toBe('canter_3t');
    expect(quote.alternatives.map((a) => a.vehicleClass)).not.toContain('pickup_single');
  });

  it('warns when a cheaper vehicle would be a tight single-trip fit', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    const quote = priceQuote(config, makeRequest({
      route: { distanceKm: 4, durationInTrafficMin: 10 },
      load: basket(['mattress_double', 2]),
      forceVehicleClass: 'canter_3t',
    }), CTX);
    expect(quote.alternatives.find((a) => a.vehicleClass === 'pickup_single')?.warning)
      .toBe('Tight fit — no room to spare');
  });

  it('offers the next size up for customers who would rather not risk it', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    const quote = priceQuote(config, makeRequest({ load: basket(['carton_medium', 3]) }), CTX);
    expect(quote.alternatives.find((a) => a.vehicleClass === 'canter_3t')?.warning).toBe('More room than you need');
  });

  it('offers no cheaper option when splitting the load costs more than one big trip', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    const quote = priceQuote(config, makeRequest({
      route: { distanceKm: 40, durationInTrafficMin: 70 },
      load: basket(['sofa_5seater', 2], ['wardrobe_2door', 2], ['carton_medium', 20]),
    }), CTX);
    expect(quote.alternatives.filter((a) => a.vehicleClass === 'pickup_single')).toEqual([]);
  });

  it('never offers a smaller vehicle that physically cannot take the load', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    // 4 m timber needs a canter bed; splitting the load does not shorten it.
    const quote = priceQuote(config, makeRequest({ load: basket(['timber_lengths_10', 2]) }), CTX);
    expect(quote.vehicleClass).toBe('canter_3t');
    expect(quote.alternatives.map((a) => a.vehicleClass)).not.toContain('pickup_single');
  });

  it('offers nothing larger when already on the biggest vehicle available', () => {
    const config = configWithClasses('canter_3t');
    const quote = priceQuote(config, makeRequest(), CTX);
    expect(quote.alternatives).toEqual([]);
  });

  it('does not offer a larger vehicle that still cannot take the load', () => {
    const config = configWith({
      vehicleClasses: (classes) => {
        classes.forEach((c) => { c.enabled = ['pickup_single', 'canter_3t'].includes(c.code); });
        // A canter that is roomier but cannot take a passenger.
        classes.find((c) => c.code === 'canter_3t')!.passengerCapacity = 0;
      },
    });
    const quote = priceQuote(config, makeRequest({ passengers: 1, load: basket(['carton_medium', 2]) }), CTX);
    expect(quote.vehicleClass).toBe('pickup_single');
    expect(quote.alternatives).toEqual([]);
  });
});

describe('anti-gaming — never let one side\'s word be final (§4)', () => {
  it('gives a new customer the full tolerance band', () => {
    expect(evaluateCustomerRisk(defaultConfig, undefined))
      .toEqual({ toleranceFraction: 0.2, requiresPhotoEstimate: false, requiresPrepay: false });
  });

  it('does not judge a customer on a thin record', () => {
    expect(evaluateCustomerRisk(defaultConfig, { underDeclarationRate: 1, observations: 2 }).toleranceFraction).toBe(0.2);
  });

  it('leaves an honest customer alone', () => {
    expect(evaluateCustomerRisk(defaultConfig, { underDeclarationRate: 0.1, observations: 20 }).requiresPrepay).toBe(false);
  });

  it('is exact at the threshold — at it, not over it, is still honest', () => {
    expect(evaluateCustomerRisk(defaultConfig, { underDeclarationRate: 0.25, observations: 20 }).requiresPrepay).toBe(false);
  });

  it('takes the tolerance band away from a habitual under-declarer', () => {
    expect(evaluateCustomerRisk(defaultConfig, { underDeclarationRate: 0.6, observations: 20 }))
      .toEqual({ toleranceFraction: 0, requiresPhotoEstimate: true, requiresPrepay: true });
  });

  it('carries the decision into the quote the customer is shown', () => {
    const quote = priceQuote(defaultConfig, makeRequest(), { ...CTX, customerRisk: { underDeclarationRate: 0.9, observations: 30 } });
    expect(quote.meta).toMatchObject({ toleranceFraction: 0, requiresPhotoEstimate: true, requiresPrepay: true });
  });
});

describe('quote metadata for downstream services', () => {
  it('carries what dispatch and the tolerance check need', () => {
    const quote = priceQuote(defaultConfig, makeRequest({ load: basket(['sack_50kg_cement', 40]) }), CTX);
    expect(quote.meta).toMatchObject({
      volumeM3: 1.4, weightKg: 2000, freeHandlingMinutes: 30, loadFactorMultiplier: 1.08,
    });
    expect(quote.meta.weightFraction).toBeCloseTo(0.667, 2);
  });
});

describe('the price shown is the price paid (§2.2)', () => {
  it('never charges more than it quoted for the same inputs', () => {
    const request = makeRequest({
      route: { distanceKm: 31.2, durationInTrafficMin: 55, additionalStops: 1 },
      load: basket(['fridge_double_door', 1], ['sofa_3seater', 1], ['carton_medium', 9]),
      helpers: 2, pickupFloors: 2, dropoffFloors: 1, demandMultiplier: 1.3,
      scheduledFor: new Date(2026, 2, 4, 23, 0), businessTier: 'silver',
    });
    const config = configWithClasses('pickup_single', 'panel_van', 'canter_3t');
    const first = priceQuote(config, request, CTX);
    const second = priceQuote(config, request, { ...CTX, quoteId: 'q_other' });
    expect(second.total).toBe(first.total);
  });

  it('adds up — the breakdown reconciles to the total', () => {
    const request = makeRequest({
      route: { distanceKm: 31.2, durationInTrafficMin: 55, additionalStops: 1 },
      load: basket(['fridge_double_door', 1], ['carton_medium', 9]),
      helpers: 1, pickupFloors: 2, forceVehicleClass: 'panel_van',
    });
    const quote = priceQuote(defaultConfig, request, CTX);
    const sum = quote.breakdown.reduce((total, line) => total + line.amount, 0);
    expect(Math.abs(quote.total - sum)).toBeLessThanOrEqual(5);
  });
});
