import { describe, it, expect } from 'vitest';
import { defaultConfig } from '@moveon/shared';
import { priceQuote } from './quote.js';
import { confirmLoad, buildRequote, updateRate, customerLosesTolerance, driverNeedsApproval } from './tolerance.js';
import { UnknownVehicleClassError } from './errors.js';
import { CTX, makeRequest, basket, configWith, configWithClasses, bareProvider } from './testing.js';

const bookedCanter = () => priceQuote(configWithClasses('pickup_single', 'canter_3t'), makeRequest({
  route: { distanceKm: 20, durationInTrafficMin: 40 },
  load: basket(['mattress_double', 10]),  // 4.5 m³, 250 kg -> canter
}), CTX);

const config = configWithClasses('pickup_single', 'canter_3t');

describe('driver confirmation at pickup (§4 layer 4)', () => {
  it('proceeds unchanged when the load matches', () => {
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 4.5, weightKg: 250 });
    expect(result.outcome).toBe('matches');
    expect(result.underDeclared).toBe(false);
    expect(result.requiresOpsApproval).toBe(false);
  });

  it('treats a smaller-than-booked load as a match, not a refund negotiation', () => {
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 3, weightKg: 200 });
    expect(result.outcome).toBe('matches');
    expect(result.overrunFraction).toBeLessThan(0);
  });

  it('absorbs a slightly bigger load silently — this buys peace at scale', () => {
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 5.2, weightKg: 300 });
    expect(result.outcome).toBe('absorbed');
    expect(result.underDeclared).toBe(false);
    expect(result.message).toContain('no change to your price');
  });

  it('is exact at the tolerance edge — 20% over is still absorbed cleanly', () => {
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 4.5 * 1.2, weightKg: 300 });
    expect(result.outcome).toBe('absorbed');
    expect(result.underDeclared).toBe(false);
  });

  it('still honours the price past the band while logging the under-declaration', () => {
    // The vehicle can do the job, so the customer is not charged more — but it counts.
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 8, weightKg: 500 });
    expect(result.outcome).toBe('absorbed');
    expect(result.underDeclared).toBe(true);
    expect(result.message).toContain('Proceeding at the quoted price');
  });

  it('re-quotes only when the dispatched vehicle genuinely cannot do it', () => {
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 20, weightKg: 900 });
    expect(result.outcome).toBe('requote_required');
    expect(result.underDeclared).toBe(true);
    // Nothing bigger is switched on, and ten pickup trips is not a re-quote.
    expect(result.suggestedVehicleClass).toBeUndefined();
  });

  it('names the bigger vehicle when one is available', () => {
    const wide = configWith({ vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = true; }) });
    const quote = priceQuote(wide, makeRequest({ load: basket(['carton_medium', 4]) }), CTX);
    const result = confirmLoad(wide, quote, { volumeM3: 9, weightKg: 600 });
    expect(result.outcome).toBe('requote_required');
    expect(result.suggestedVehicleClass).toBe('canter_3t');
    expect(result.message).toContain('new price for a canter_3t');
  });

  it('hands an impossible load to ops instead of the app', () => {
    const result = confirmLoad(config, bookedCanter(), { volumeM3: 60, weightKg: 20000, minBedLengthM: 12 });
    expect(result.suggestedVehicleClass).toBeUndefined();
    expect(result.message).toContain('Ops will call the customer');
  });

  it('re-checks enclosure at pickup, not just volume', () => {
    // Booked on an open pickup; at the roadside the load turns out to need cover.
    const wide = configWith({ vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = true; }) });
    const quote = priceQuote(wide, makeRequest({ load: basket(['carton_medium', 4]), forceVehicleClass: 'pickup_single' }), CTX);
    const result = confirmLoad(wide, quote, { volumeM3: 0.3, weightKg: 40, requiresEnclosed: true });
    expect(result.outcome).toBe('requote_required');
    expect(result.suggestedVehicleClass).toBe('car_boot');
  });

  it('re-checks that the customer can still ride along', () => {
    const wide = configWith({ vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = true; }) });
    const quote = priceQuote(wide, makeRequest({ load: basket(['carton_medium', 4]) }), CTX);
    expect(confirmLoad(wide, quote, { volumeM3: 0.3, weightKg: 40 }, { passengers: 9 }).outcome).toBe('requote_required');
  });

  it('rejects a quote naming a vehicle class that no longer exists', () => {
    const quote = { ...bookedCanter(), vehicleClass: 'hovercraft' };
    expect(() => confirmLoad(config, quote, { volumeM3: 1, weightKg: 1 })).toThrow(UnknownVehicleClassError);
  });

  it('does not divide by zero on a quote with no declared volume', () => {
    const quote = { ...bookedCanter(), meta: { ...bookedCanter().meta, volumeM3: 0 } };
    expect(confirmLoad(config, quote, { volumeM3: 2, weightKg: 100 }).overrunFraction).toBe(0);
  });

  it('honours a customer who has already lost their tolerance band', () => {
    const quote = bookedCanter();
    const strict = { ...quote, meta: { ...quote.meta, toleranceFraction: 0 } };
    expect(confirmLoad(config, strict, { volumeM3: 4.6, weightKg: 260 }).underDeclared).toBe(true);
  });
});

describe('driver over-reporting is policed too — never one side\'s word (§4)', () => {
  const bigLoad = { volumeM3: 20, weightKg: 900 };

  it('trusts a driver with a clean record', () => {
    expect(confirmLoad(config, bookedCanter(), bigLoad, { driverRisk: { overReportRate: 0.05, observations: 40 } }).requiresOpsApproval).toBe(false);
  });

  it('trusts a driver with no record at all', () => {
    expect(confirmLoad(config, bookedCanter(), bigLoad).requiresOpsApproval).toBe(false);
  });

  it('does not judge a driver on a thin record', () => {
    expect(confirmLoad(config, bookedCanter(), bigLoad, { driverRisk: { overReportRate: 1, observations: 2 } }).requiresOpsApproval).toBe(false);
  });

  it('is exact at the threshold', () => {
    expect(confirmLoad(config, bookedCanter(), bigLoad, { driverRisk: { overReportRate: 0.2, observations: 40 } }).requiresOpsApproval).toBe(false);
  });

  it('sends a habitual over-reporter\'s re-quote to ops', () => {
    expect(confirmLoad(config, bookedCanter(), bigLoad, { driverRisk: { overReportRate: 0.5, observations: 40 } }).requiresOpsApproval).toBe(true);
  });
});

describe('the re-quote the customer approves in-app', () => {
  it('prices the load the driver actually found, and states the difference', () => {
    const wide = configWith({ vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = true; }) });
    const request = makeRequest({ load: basket(['carton_medium', 4]) });
    const original = priceQuote(wide, request, CTX);
    const result = buildRequote(wide, original, request, { volumeM3: 9, weightKg: 600 }, { ...CTX, quoteId: 'q_requote' });

    expect(result.revised.quoteId).toBe('q_requote');
    expect(result.revised.vehicleClass).toBe('canter_3t');
    expect(result.difference).toBe(result.revised.total - original.total);
    expect(result.difference).toBeGreaterThan(0);
  });

  it('pays the driver a dead-run fee if the customer walks — never argued over', () => {
    const request = makeRequest({ load: basket(['mattress_double', 10]) });
    const original = priceQuote(config, request, CTX);
    const result = buildRequote(config, original, request, { volumeM3: 5, weightKg: 300 }, CTX);
    expect(result.deadRunFeeIfCancelled).toBe(1200);
  });

  it('carries an enclosure requirement discovered at pickup into the new price', () => {
    const wide = configWith({ vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = true; }) });
    const request = makeRequest({ load: basket(['carton_medium', 4]) });
    const original = priceQuote(wide, request, CTX);
    const result = buildRequote(wide, original, request, { volumeM3: 3, weightKg: 200, requiresEnclosed: true }, CTX);
    expect(result.revised.breakdown.some((l) => l.code === 'enclosed')).toBe(true);
  });

  it('does not invent a dead-run fee for a class ops has since retired', () => {
    // The canter the trip was booked on no longer exists in the config at all.
    const stripped = bareProvider({
      vehicleClasses: defaultConfig.vehicleClasses().filter((c) => c.code !== 'canter_3t'),
      classPricing: defaultConfig.classPricing().filter((p) => p.vehicleClass !== 'canter_3t'),
    });
    const request = makeRequest({ load: basket(['mattress_double', 10]) });
    const original = priceQuote(config, request, CTX);
    expect(buildRequote(stripped, original, request, { volumeM3: 5, weightKg: 300 }, CTX).deadRunFeeIfCancelled).toBe(0);
  });
});

describe('reputation scoring', () => {
  it('starts at zero and records a clean trip', () => {
    expect(updateRate({ rate: 0, observations: 0 }, false)).toEqual({ rate: 0, observations: 1 });
  });

  it('records an incident', () => {
    expect(updateRate({ rate: 0, observations: 0 }, true)).toEqual({ rate: 1, observations: 1 });
  });

  it('averages over the whole history rather than overreacting to one trip', () => {
    let score = { rate: 0, observations: 0 };
    for (let i = 0; i < 9; i += 1) score = updateRate(score, false);
    score = updateRate(score, true);
    expect(score).toEqual({ rate: 0.1, observations: 10 });
  });

  it('gates the customer on the same rule the quote engine uses', () => {
    expect(customerLosesTolerance(defaultConfig, { rate: 0.9, observations: 2 })).toBe(false);
    expect(customerLosesTolerance(defaultConfig, { rate: 0.25, observations: 20 })).toBe(false);
    expect(customerLosesTolerance(defaultConfig, { rate: 0.26, observations: 20 })).toBe(true);
  });

  it('gates the driver symmetrically', () => {
    expect(driverNeedsApproval(defaultConfig, { rate: 0.9, observations: 2 })).toBe(false);
    expect(driverNeedsApproval(defaultConfig, { rate: 0.2, observations: 20 })).toBe(false);
    expect(driverNeedsApproval(defaultConfig, { rate: 0.21, observations: 20 })).toBe(true);
  });
});
