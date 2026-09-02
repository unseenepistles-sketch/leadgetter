import { describe, it, expect } from 'vitest';
import { defaultConfig } from '@moveon/shared';
import {
  resolveLoad, fromCatalogBasket, fromFillLevel, fromPhotoEstimate,
  expandBundle, assessPhotoConfidence, buildPhotoObservation,
} from './load.js';
import { UnknownCatalogItemError, UnknownVehicleClassError } from './errors.js';
import { configWith } from './testing.js';

describe('layer 1 — item catalog', () => {
  it('sums volume and infers mass without ever asking the customer for it', () => {
    const profile = fromCatalogBasket(defaultConfig, [
      { itemId: 'mattress_double', quantity: 1 },
      { itemId: 'carton_medium', quantity: 5 },
    ]);
    expect(profile.volumeM3).toBeCloseTo(0.45 + 5 * 0.08, 3);
    expect(profile.weightKg).toBeCloseTo(25 + 5 * 15, 1);
    expect(profile.summary).toBe('Your 6 items need ~0.9 m³');
  });

  it('reads correctly for a single piece', () => {
    expect(fromCatalogBasket(defaultConfig, [{ itemId: 'carton_medium', quantity: 1 }]).summary)
      .toBe('Your 1 item needs ~0.1 m³');
  });

  it('rejects an unknown item rather than guessing at its size', () => {
    expect(() => fromCatalogBasket(defaultConfig, [{ itemId: 'nope', quantity: 1 }]))
      .toThrow(UnknownCatalogItemError);
  });

  it('propagates fragile and enclosed requirements from any one item', () => {
    const profile = fromCatalogBasket(defaultConfig, [
      { itemId: 'carton_medium', quantity: 1 },
      { itemId: 'display_fridge', quantity: 1 },
    ]);
    expect(profile.fragile).toBe(true);
    expect(profile.requiresEnclosed).toBe(true);
  });

  it('does not force a van on household goods that travel fine under a tarp', () => {
    // A fridge or a TV goes on an open canter here every day. `fragile` is the
    // real signal; enclosure is the customer's call (see the quote request).
    const profile = fromCatalogBasket(defaultConfig, [
      { itemId: 'fridge_double_door', quantity: 1 },
      { itemId: 'tv_boxed', quantity: 1 },
    ]);
    expect(profile.fragile).toBe(true);
    expect(profile.requiresEnclosed).toBe(false);
  });

  it('leaves flags clear when nothing in the basket sets them', () => {
    const profile = fromCatalogBasket(defaultConfig, [{ itemId: 'carton_medium', quantity: 2 }]);
    expect(profile).toMatchObject({ fragile: false, requiresEnclosed: false, densityCritical: false, minBedLengthM: 0 });
  });

  it('flags a density-critical basket — cement is the case that breaks naive apps', () => {
    const profile = fromCatalogBasket(defaultConfig, [{ itemId: 'sack_50kg_cement', quantity: 20 }]);
    expect(profile.densityCritical).toBe(true);
    expect(profile.weightKg).toBe(1000);
    expect(profile.volumeM3).toBeCloseTo(0.7, 3);
  });

  it('takes the longest item as the bed length — lengths never sum', () => {
    const profile = fromCatalogBasket(defaultConfig, [
      { itemId: 'iron_sheets_10', quantity: 2 },  // 3.0 m
      { itemId: 'ppr_pipes_10', quantity: 1 },    // 6.0 m
      { itemId: 'office_desk', quantity: 1 },     // 1.6 m
    ]);
    expect(profile.minBedLengthM).toBe(6);
  });

  it('counts handling units, because pieces are what a loader carries', () => {
    const profile = fromCatalogBasket(defaultConfig, [{ itemId: 'building_blocks_50', quantity: 2 }]);
    expect(profile.handlingUnits).toBe(100);
  });

  it('routes through resolveLoad', () => {
    const profile = resolveLoad(defaultConfig, { method: 'catalog', lines: [{ itemId: 'carton_medium', quantity: 1 }] });
    expect(profile.volumeM3).toBeCloseTo(0.08, 3);
  });
});

describe('bundles — one tap, done', () => {
  it('expands a bundle into basket lines', () => {
    const lines = expandBundle(defaultConfig, 'bedsitter_move');
    expect(lines.length).toBeGreaterThan(3);
    expect(fromCatalogBasket(defaultConfig, lines).volumeM3).toBeGreaterThan(0);
  });

  it('rejects an unknown bundle', () => {
    expect(() => expandBundle(defaultConfig, 'not_a_bundle')).toThrow(UnknownCatalogItemError);
  });

  it('every shipped bundle resolves and fits some vehicle', () => {
    for (const bundle of defaultConfig.bundles()) {
      const profile = fromCatalogBasket(defaultConfig, expandBundle(defaultConfig, bundle.id));
      expect(profile.volumeM3).toBeGreaterThan(0);
    }
  });
});

describe('layer 2 — fill-level slider', () => {
  it.each([
    ['quarter', 0.25, 'a quarter'],
    ['half', 0.5, 'half'],
    ['three_quarter', 0.75, 'three quarters'],
    ['full', 1.0, 'a full load'],
    ['overflowing', 1.25, 'more than a full load'],
  ] as const)('maps %s to %f of the reference vehicle', (level, fraction, phrase) => {
    const profile = fromFillLevel(defaultConfig, 'canter_3t', level);
    expect(profile.volumeM3).toBeCloseTo(12 * fraction, 3);
    expect(profile.summary).toContain(phrase);
  });

  it('infers mass from a household density, capped by the payload ceiling', () => {
    // 12 m³ x 150 kg/m³ = 1800 kg, under the canter's 3000 kg ceiling.
    expect(fromFillLevel(defaultConfig, 'canter_3t', 'full').weightKg).toBe(1800);
  });

  it('never infers a mass above what the reference vehicle may carry', () => {
    const dense = configWith({ pricing: (p) => { p.estimation.nominalDensityKgPerM3 = 5000; } });
    const profile = fromFillLevel(dense, 'canter_3t', 'half');
    expect(profile.weightKg).toBe(3000 * 0.5);
  });

  it('rejects an unknown reference vehicle', () => {
    expect(() => fromFillLevel(defaultConfig, 'hovercraft', 'half')).toThrow(UnknownVehicleClassError);
  });

  it('always reports at least one handling unit', () => {
    const tiny = configWith({ pricing: (p) => { p.estimation.handlingUnitsPerM3 = 0.0001; } });
    expect(fromFillLevel(tiny, 'boda', 'quarter').handlingUnits).toBe(1);
  });

  it('routes through resolveLoad', () => {
    const profile = resolveLoad(defaultConfig, { method: 'fill_level', referenceClass: 'canter_3t', fillLevel: 'half' });
    expect(profile.volumeM3).toBeCloseTo(6, 3);
  });
});

describe('layer 3 — photo estimate', () => {
  const estimate = {
    estimatedVolumeM3: 3.4, estimatedWeightKg: 400, confidence: 0.8,
    requiresEnclosed: false, fragile: false,
  };

  it('carries the vision estimate through as a load profile', () => {
    const profile = fromPhotoEstimate(defaultConfig, estimate);
    expect(profile.volumeM3).toBe(3.4);
    expect(profile.summary).toBe('From your photos: about 3.4 m³');
  });

  it('carries fragility and enclosure through', () => {
    const profile = fromPhotoEstimate(defaultConfig, { ...estimate, fragile: true, requiresEnclosed: true });
    expect(profile).toMatchObject({ fragile: true, requiresEnclosed: true });
  });

  it('always reports at least one handling unit', () => {
    expect(fromPhotoEstimate(defaultConfig, { ...estimate, estimatedVolumeM3: 0.001 }).handlingUnits).toBe(1);
  });

  it('routes through resolveLoad', () => {
    const profile = resolveLoad(defaultConfig, { method: 'photo', photoIds: ['p1'], ...estimate });
    expect(profile.volumeM3).toBe(3.4);
  });

  it('offers a confident read as a suggestion the customer confirms', () => {
    expect(assessPhotoConfidence(defaultConfig, 0.9).presentation).toBe('suggest');
    expect(assessPhotoConfidence(defaultConfig, 0.55).presentation).toBe('suggest');
  });

  it('falls back to the catalog rather than guess from a bad photo', () => {
    const result = assessPhotoConfidence(defaultConfig, 0.3);
    expect(result.presentation).toBe('fall_back');
    expect(result.message).toContain('fill slider');
  });

  it('logs the estimate against the driver-confirmed truth — this is the training set', () => {
    const observation = buildPhotoObservation(
      { photoIds: ['a', 'b'], estimatedVolumeM3: 3.4, confidence: 0.8 },
      { confirmedVolumeM3: 4.1, confirmedVehicleClass: 'canter_3t' },
      new Date('2026-03-04T10:00:00.000Z'),
    );
    expect(observation).toEqual({
      photoIds: ['a', 'b'], estimatedVolumeM3: 3.4, confidence: 0.8,
      confirmedVolumeM3: 4.1, confirmedVehicleClass: 'canter_3t',
      observedAt: '2026-03-04T10:00:00.000Z',
    });
  });
});
