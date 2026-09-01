import { describe, it, expect } from 'vitest';
import { StaticConfigProvider, defaultConfig, classesBySize, findVehicleClass, findClassPricing, ConfigError } from './config.js';

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

const build = (mutate: (parts: any) => void) => {
  const parts = {
    vehicleClasses: clone(defaultConfig.vehicleClasses()),
    classPricing: clone(defaultConfig.classPricing()),
    pricing: clone(defaultConfig.pricing()),
    catalog: clone(defaultConfig.catalog()),
    bundles: clone(defaultConfig.bundles()),
  };
  mutate(parts);
  return () => new StaticConfigProvider(parts);
};

describe('the shipped seed configuration', () => {
  it('validates', () => {
    expect(() => new StaticConfigProvider()).not.toThrow();
  });

  it('launches with exactly two adjacent classes (§12)', () => {
    const enabled = defaultConfig.vehicleClasses().filter((c) => c.enabled);
    expect(enabled.map((c) => c.code).sort()).toEqual(['canter_3t', 'pickup_single']);
  });

  it('carries the vehicle envelopes from the brief', () => {
    expect(findVehicleClass(defaultConfig, 'canter_3t')).toMatchObject({ volumeM3: 12, payloadKg: 3000 });
    expect(findVehicleClass(defaultConfig, 'boda')).toMatchObject({ volumeM3: 0.15, payloadKg: 50 });
    expect(findVehicleClass(defaultConfig, 'lorry_10t')).toMatchObject({ volumeM3: 40, payloadKg: 10000 });
  });

  it('orders classes by size for recommendation', () => {
    const sizes = classesBySize(defaultConfig, { onlyEnabled: false }).map((c) => c.volumeM3);
    expect(sizes).toEqual([...sizes].sort((a, b) => a - b));
  });

  it('has a rate card for every class', () => {
    for (const c of defaultConfig.vehicleClasses()) expect(findClassPricing(defaultConfig, c.code)).toBeDefined();
  });

  it('flags the two edge cases the brief calls out', () => {
    expect(defaultConfig.catalog().find((i) => i.id === 'building_blocks_50')?.densityCritical).toBe(true);
    expect(defaultConfig.catalog().find((i) => i.id === 'iron_sheets_10')).toMatchObject({ lengthCritical: true, minBedLengthM: 3 });
  });

  it('returns nothing for a class that does not exist', () => {
    expect(findVehicleClass(defaultConfig, 'nope')).toBeUndefined();
    expect(findClassPricing(defaultConfig, 'nope')).toBeUndefined();
  });
});

describe('ops guardrails — the mistakes someone will make in the pricing screen at 11pm', () => {
  it('rejects a malformed rate table outright, rather than mid-quote', () => {
    expect(build((p) => { p.classPricing[0].baseFare = -5; })).toThrow(ConfigError);
  });

  it('rejects a distance rate that rises instead of tapering', () => {
    expect(build((p) => { p.classPricing[0].distanceBands[1].ratePerKm = 9999; })).toThrow(/must taper/);
  });

  it('rejects distance bands that do not ascend', () => {
    expect(build((p) => { p.classPricing[0].distanceBands[1].uptoKm = 1; })).toThrow(/must ascend/);
  });

  it('rejects a rate card whose last band is not open-ended', () => {
    expect(build((p) => { p.classPricing[0].distanceBands.at(-1).uptoKm = 500; })).toThrow(/must be open-ended/);
  });

  it('rejects an open-ended band that is not last', () => {
    expect(build((p) => { p.classPricing[0].distanceBands[0].uptoKm = null; })).toThrow(/must be last/);
  });

  it('rejects a class with no rate card', () => {
    expect(build((p) => { p.classPricing = p.classPricing.filter((r: any) => r.vehicleClass !== 'boda'); })).toThrow(/no pricing row/);
  });

  it('rejects a rate card for a class that does not exist', () => {
    expect(build((p) => { p.classPricing.push({ ...p.classPricing[0], vehicleClass: 'ghost' }); })).toThrow(/unknown vehicle class/);
  });

  it('rejects duplicate class codes', () => {
    expect(build((p) => { p.vehicleClasses.push({ ...p.vehicleClasses[0] }); })).toThrow(/duplicate vehicle class codes/);
  });

  it('rejects load factor bands that do not ascend', () => {
    expect(build((p) => { p.pricing.loadFactor.bands[1].upToFraction = 0.1; })).toThrow(/load factor bands must ascend/);
  });

  it('rejects load factor bands that stop short of a full vehicle', () => {
    expect(build((p) => { p.pricing.loadFactor.bands.at(-1).upToFraction = 0.9; })).toThrow(/must cover a full vehicle/);
  });

  it('rejects duplicate catalog ids', () => {
    expect(build((p) => { p.catalog.push({ ...p.catalog[0] }); })).toThrow(/duplicate catalog item ids/);
  });

  it('rejects a length-critical item with no bed length', () => {
    expect(build((p) => { delete p.catalog.find((i: any) => i.id === 'iron_sheets_10').minBedLengthM; })).toThrow(/no minBedLengthM/);
  });

  it('rejects a bundle pointing at an item that was deleted', () => {
    expect(build((p) => { p.bundles[0].items[0].itemId = 'deleted_item'; })).toThrow(/unknown item/);
  });

  it('rejects switching every vehicle off — nothing could be dispatched', () => {
    expect(build((p) => { p.vehicleClasses.forEach((c: any) => { c.enabled = false; }); })).toThrow(/nothing can be dispatched/);
  });

  it('names the offending field so ops can fix it', () => {
    try {
      build((p) => { p.pricing.currency = 'X'; })();
      expect.unreachable('should have rejected');
    } catch (error) {
      expect((error as Error).message).toContain('currency');
    }
  });
});
