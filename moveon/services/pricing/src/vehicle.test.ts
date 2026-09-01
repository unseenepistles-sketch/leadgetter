import { describe, it, expect } from 'vitest';
import { defaultConfig, findVehicleClass, type LoadProfile } from '@moveon/shared';
import { assessFit, recommendVehicle } from './vehicle.js';
import { NoSuitableVehicleError } from './errors.js';
import { fromCatalogBasket } from './load.js';
import { configWith, configWithClasses, bareProvider } from './testing.js';

const profile = (overrides: Partial<LoadProfile> = {}): LoadProfile => ({
  volumeM3: 1, weightKg: 100, fragile: false, requiresEnclosed: false,
  handlingUnits: 1, densityCritical: false, minBedLengthM: 0, summary: 'test load',
  ...overrides,
});

const canter = findVehicleClass(defaultConfig, 'canter_3t')!;
const pickup = findVehicleClass(defaultConfig, 'pickup_single')!;
const van = findVehicleClass(defaultConfig, 'panel_van')!;

describe('fit is a hard filter, never a soft score (§7)', () => {
  it('accepts a load inside every constraint', () => {
    const fit = assessFit(defaultConfig, canter, profile({ volumeM3: 6, weightKg: 1500 }), { passengers: 0 });
    expect(fit.fits).toBe(true);
    expect(fit.failures).toEqual([]);
    expect(fit.volumeFraction).toBeCloseTo(0.5, 4);
    expect(fit.weightFraction).toBeCloseTo(0.5, 4);
  });

  it('rejects on volume', () => {
    expect(assessFit(defaultConfig, pickup, profile({ volumeM3: 9 }), { passengers: 0 }).failures).toContain('volume');
  });

  it('rejects on payload even when the load barely fills the bed', () => {
    // Twenty sacks of cement: a fifth of a pickup by space, over its legal ceiling by mass.
    const fit = assessFit(defaultConfig, pickup, profile({ volumeM3: 0.7, weightKg: 1400 }), { passengers: 0 });
    expect(fit.failures).toEqual(['payload']);
    expect(fit.fits).toBe(false);
  });

  it('rejects on bed length — a 6 m pipe does not become shorter', () => {
    expect(assessFit(defaultConfig, pickup, profile({ minBedLengthM: 6 }), { passengers: 0 }).failures).toContain('bed_length');
  });

  it('rejects an open bed for a load that must stay dry', () => {
    expect(assessFit(defaultConfig, canter, profile({ requiresEnclosed: true }), { passengers: 0 }).failures).toContain('enclosed');
    expect(assessFit(defaultConfig, van, profile({ requiresEnclosed: true }), { passengers: 0 }).failures).not.toContain('enclosed');
  });

  it('rejects when the customer cannot ride along', () => {
    expect(assessFit(defaultConfig, pickup, profile(), { passengers: 3 }).failures).toContain('passengers');
  });

  it('accumulates every reason it failed, for ops to read out', () => {
    const fit = assessFit(defaultConfig, pickup, profile({ volumeM3: 40, weightKg: 9000, minBedLengthM: 8, requiresEnclosed: true }), { passengers: 4 });
    expect(fit.failures).toEqual(['volume', 'payload', 'bed_length', 'enclosed', 'passengers']);
  });

  it('is exact at the boundary — a load filling the vehicle exactly still fits', () => {
    expect(assessFit(defaultConfig, canter, profile({ volumeM3: 12, weightKg: 3000 }), { passengers: 0 }).fits).toBe(true);
  });

  it('fails a hair over the boundary', () => {
    expect(assessFit(defaultConfig, canter, profile({ volumeM3: 12.001 }), { passengers: 0 }).fits).toBe(false);
    expect(assessFit(defaultConfig, canter, profile({ weightKg: 3000.1 }), { passengers: 0 }).fits).toBe(false);
  });
});

describe('density-critical detection', () => {
  it('flags a load whose mass commits the vehicle before its space does', () => {
    const fit = assessFit(defaultConfig, canter, profile({ volumeM3: 1, weightKg: 2700 }), { passengers: 0 });
    expect(fit.densityCritical).toBe(true);
    expect(fit.bindingFraction).toBeCloseTo(0.9, 4);
  });

  it('does not flag a bulky-but-light load', () => {
    const fit = assessFit(defaultConfig, canter, profile({ volumeM3: 11, weightKg: 300 }), { passengers: 0 });
    expect(fit.densityCritical).toBe(false);
    expect(fit.bindingFraction).toBeCloseTo(11 / 12, 4);
  });

  it('does not flag heavy loads that are still well short of the ceiling', () => {
    // Weight binds, but at 60% there is real headroom — that is a load factor, not a density case.
    expect(assessFit(defaultConfig, canter, profile({ volumeM3: 1, weightKg: 1800 }), { passengers: 0 }).densityCritical).toBe(false);
  });

  it('respects the configured threshold', () => {
    const strict = configWith({ pricing: (p) => { p.loadFactor.densityCriticalThreshold = 0.5; } });
    expect(assessFit(strict, canter, profile({ volumeM3: 1, weightKg: 1800 }), { passengers: 0 }).densityCritical).toBe(true);
  });
});

describe('recommendVehicle — smallest thing that can legally do the job', () => {
  it('picks the smallest fitting class, not the roomiest', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    const result = recommendVehicle(config, profile({ volumeM3: 1, weightKg: 300 }), { passengers: 0 });
    expect(result.assessment.vehicleClass.code).toBe('pickup_single');
    expect(result.tripsRequired).toBe(1);
  });

  it('steps up to the canter when mass rules the pickup out', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    const result = recommendVehicle(config, profile({ volumeM3: 0.8, weightKg: 1600 }), { passengers: 0 });
    expect(result.assessment.vehicleClass.code).toBe('canter_3t');
  });

  it('explains itself in the customer\'s terms', () => {
    const load = fromCatalogBasket(defaultConfig, [{ itemId: 'mattress_double', quantity: 2 }]);
    expect(recommendVehicle(defaultConfig, load, { passengers: 0 }).reason)
      .toBe('Your 2 items need ~0.9 m³ — that\'s a Pickup (single cab)');
  });

  it('says so when weight, not bulk, chose the vehicle', () => {
    const result = recommendVehicle(configWithClasses('canter_3t'), profile({ volumeM3: 1, weightKg: 2800 }), { passengers: 0 });
    expect(result.reason).toContain('the weight fills a Canter 3t first');
  });

  it('splits an oversized load across trips rather than refusing it', () => {
    const config = configWithClasses('pickup_single');
    const result = recommendVehicle(config, profile({ volumeM3: 5, weightKg: 400 }), { passengers: 0 });
    expect(result.tripsRequired).toBe(3);
    expect(result.reason).toContain('across 3 trips');
    expect(result.assessment.volumeFraction).toBeCloseTo(2.5 / 3, 3);
  });

  it('splits on whichever constraint needs the most trips', () => {
    const config = configWithClasses('pickup_single');
    // 1.5 m³ is one trip by space; 3500 kg is four by mass.
    const result = recommendVehicle(config, profile({ volumeM3: 1.5, weightKg: 3500 }), { passengers: 0 });
    expect(result.tripsRequired).toBe(4);
  });

  it('refuses a load no available vehicle can take, whatever the trip count', () => {
    const config = configWithClasses('pickup_single');
    expect(() => recommendVehicle(config, profile({ minBedLengthM: 9 }), { passengers: 0 }))
      .toThrow(NoSuitableVehicleError);
  });

  it('names every reason it cannot be done', () => {
    const config = configWithClasses('pickup_single');
    try {
      recommendVehicle(config, profile({ volumeM3: 50, weightKg: 9000, minBedLengthM: 9, requiresEnclosed: true }), { passengers: 4 });
      expect.unreachable('should have refused');
    } catch (error) {
      expect((error as NoSuitableVehicleError).reasons).toEqual([
        'the load is larger than every available vehicle',
        'the load is heavier than any available vehicle may legally carry',
        'nothing available has a bed of 9 m',
        'this load needs an enclosed vehicle and none is available',
        'no available vehicle seats 4 passenger(s) alongside the load',
      ]);
    }
  });

  it('refuses an enclosure requirement no open bed can satisfy', () => {
    const config = configWithClasses('pickup_single', 'canter_3t');
    expect(() => recommendVehicle(config, profile({ requiresEnclosed: true }), { passengers: 0 }))
      .toThrow(/enclosed vehicle/);
  });

  it('can search classes ops has not switched on yet', () => {
    const result = recommendVehicle(defaultConfig, profile({ requiresEnclosed: true, volumeM3: 3 }), { passengers: 0 }, { onlyEnabled: false });
    expect(result.assessment.vehicleClass.code).toBe('panel_van');
  });

  it('refuses rather than crashing when no vehicle classes exist at all', () => {
    expect(() => recommendVehicle(bareProvider({ vehicleClasses: [] }), profile(), { passengers: 0 }))
      .toThrow(/no vehicle classes are available/);
  });
});
