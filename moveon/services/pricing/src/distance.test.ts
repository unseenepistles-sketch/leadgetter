import { describe, it, expect } from 'vitest';
import { defaultConfig, findClassPricing, type DistanceBand } from '@moveon/shared';
import { priceDistance, isIntercity } from './distance.js';

const canter = findClassPricing(defaultConfig, 'canter_3t')!;
const bands = canter.distanceBands;

describe('tapered distance pricing (§6)', () => {
  it('charges nothing for a zero-distance trip', () => {
    const result = priceDistance(bands, 0);
    expect(result.total).toBe(0);
    expect(result.segments).toEqual([]);
  });

  it('bills only the first band inside the first band', () => {
    const result = priceDistance(bands, 3);
    expect(result.segments).toHaveLength(1);
    expect(result.total).toBe(3 * 160);
  });

  describe('band boundaries', () => {
    // Every boundary is a place money can leak. All of them are pinned.
    it.each([
      { km: 5, expected: 5 * 160, segments: 1 },
      { km: 5.5, expected: 5 * 160 + 0.5 * 88, segments: 2 },
      { km: 20, expected: 5 * 160 + 15 * 88, segments: 2 },
      { km: 20.5, expected: 5 * 160 + 15 * 88 + 0.5 * 68, segments: 3 },
      { km: 80, expected: 5 * 160 + 15 * 88 + 60 * 68, segments: 3 },
      { km: 80.5, expected: 5 * 160 + 15 * 88 + 60 * 68 + 0.5 * 52, segments: 4 },
      { km: 200, expected: 5 * 160 + 15 * 88 + 60 * 68 + 120 * 52, segments: 4 },
    ])('bills $km km as $expected', ({ km, expected, segments }) => {
      const result = priceDistance(bands, km);
      expect(result.total).toBeCloseTo(expected, 2);
      expect(result.segments).toHaveLength(segments);
    });
  });

  it('tapers — the average rate per km falls as the trip lengthens', () => {
    const rates = [5, 20, 80, 200].map((km) => priceDistance(bands, km).total / km);
    for (let i = 1; i < rates.length; i += 1) expect(rates[i]!).toBeLessThan(rates[i - 1]!);
  });

  it('never charges a long haul at the short-haul rate', () => {
    // The bug this whole function exists to prevent: 200 km at a flat 160/km.
    expect(priceDistance(bands, 200).total).toBeLessThan(200 * bands[0]!.ratePerKm);
  });

  it('skips a zero-width band without losing the segments around it', () => {
    const degenerate: DistanceBand[] = [
      { uptoKm: 5, ratePerKm: 100, label: 'a' },
      { uptoKm: 5, ratePerKm: 90, label: 'zero width' },
      { uptoKm: null, ratePerKm: 50, label: 'rest' },
    ];
    const result = priceDistance(degenerate, 10);
    expect(result.segments.map((s) => s.label)).toEqual(['a', 'rest']);
    expect(result.total).toBe(5 * 100 + 5 * 50);
  });

  it('reports segment detail for the receipt', () => {
    const [first] = priceDistance(bands, 10).segments;
    expect(first).toMatchObject({ label: '0-5 km', km: 5, ratePerKm: 160, amount: 800 });
  });
});

describe('isIntercity', () => {
  it('is false up to and including the last bounded band', () => {
    expect(isIntercity(bands, 80)).toBe(false);
  });

  it('is true beyond it', () => {
    expect(isIntercity(bands, 80.1)).toBe(true);
  });

  it('is false when a single open-ended band prices every distance the same', () => {
    expect(isIntercity([{ uptoKm: null, ratePerKm: 60, label: 'flat' }], 500)).toBe(false);
  });
});
