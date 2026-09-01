import { describe, it, expect } from 'vitest';
import { defaultConfig } from '@moveon/shared';
import { handlingWindow, priceWaiting, priceHelperUpsell } from './handling.js';
import { UnknownVehicleClassError, PricingError } from './errors.js';
import { bareProvider, configWith } from './testing.js';

describe('the free curbside window (§5)', () => {
  it('is longer for a bigger vehicle, because the job is bigger', () => {
    expect(handlingWindow(defaultConfig, 'boda').freeMinutes).toBe(5);
    expect(handlingWindow(defaultConfig, 'pickup_single').freeMinutes).toBe(15);
    expect(handlingWindow(defaultConfig, 'canter_3t').freeMinutes).toBe(30);
    expect(handlingWindow(defaultConfig, 'lorry_7t').freeMinutes).toBe(45);
  });

  it('adds a grace period before the meter can start', () => {
    expect(handlingWindow(defaultConfig, 'canter_3t')).toEqual({
      freeMinutes: 30, graceMinutes: 3, meterStartsAfterMinutes: 33,
    });
  });

  it('rejects an unknown vehicle class', () => {
    expect(() => handlingWindow(defaultConfig, 'sled')).toThrow(UnknownVehicleClassError);
  });
});

describe('metered waiting — visible, never argued over', () => {
  it('charges nothing inside the included window', () => {
    const charge = priceWaiting(defaultConfig, 'canter_3t', 25, 0);
    expect(charge).toMatchObject({ chargeableMinutes: 0, amount: 0, waived: false });
    expect(charge.explanation).toContain('30 min of included curbside handling');
  });

  it('charges nothing at the exact moment the meter would start', () => {
    expect(priceWaiting(defaultConfig, 'canter_3t', 33, 0).amount).toBe(0);
  });

  it('waives the first overruns of the month as goodwill', () => {
    const charge = priceWaiting(defaultConfig, 'canter_3t', 50, 0);
    expect(charge.chargeableMinutes).toBe(17);
    expect(charge.amount).toBe(0);
    expect(charge.waived).toBe(true);
    expect(charge.explanation).toBe('Waiting time waived (1 of 2 this month)');
  });

  it('waives the second one too', () => {
    expect(priceWaiting(defaultConfig, 'canter_3t', 50, 1).waived).toBe(true);
  });

  it('bills the third', () => {
    const charge = priceWaiting(defaultConfig, 'canter_3t', 50, 2);
    expect(charge.waived).toBe(false);
    expect(charge.amount).toBe(17 * 25);
    expect(charge.explanation).toBe('17 min beyond the included 30 min');
  });

  it('rounds part-minutes up, so the meter is never behind the clock', () => {
    expect(priceWaiting(defaultConfig, 'canter_3t', 40.2, 5).chargeableMinutes).toBe(8);
  });

  it('bills every overrun when the goodwill budget is switched off', () => {
    const strict = configWith({ pricing: (p) => { p.handling.waivedOverrunsPerMonth = 0; } });
    expect(priceWaiting(strict, 'canter_3t', 50, 0).waived).toBe(false);
  });

  it('rejects an unknown vehicle class', () => {
    expect(() => priceWaiting(defaultConfig, 'sled', 60, 0)).toThrow(UnknownVehicleClassError);
  });

  it('fails loudly when a class has no rate card', () => {
    const config = bareProvider({ classPricing: defaultConfig.classPricing().filter((p) => p.vehicleClass !== 'canter_3t') });
    expect(() => priceWaiting(config, 'canter_3t', 60, 0)).toThrow(PricingError);
  });
});

describe('helper upsell — stairs discovered at pickup', () => {
  it('prices a helper per floor', () => {
    expect(priceHelperUpsell(defaultConfig, 1, 3)).toMatchObject({ amount: 300 + 3 * 150, helpers: 1 });
  });

  it('prices two helpers', () => {
    const result = priceHelperUpsell(defaultConfig, 2, 2);
    expect(result.amount).toBe(2 * (300 + 2 * 150));
    expect(result.explanation).toBe('2 helpers for 2 floors');
  });

  it('reads correctly for one helper and one floor', () => {
    expect(priceHelperUpsell(defaultConfig, 1, 1).explanation).toBe('1 helper for 1 floor');
  });

  it('costs nothing when none are wanted', () => {
    expect(priceHelperUpsell(defaultConfig, 0, 4)).toMatchObject({ amount: 0, helpers: 0, explanation: 'No helpers requested' });
  });

  it('caps at the configured maximum', () => {
    expect(priceHelperUpsell(defaultConfig, 9, 0).helpers).toBe(2);
  });

  it('treats nonsense input as zero rather than a credit', () => {
    expect(priceHelperUpsell(defaultConfig, -3, 2).amount).toBe(0);
    expect(priceHelperUpsell(defaultConfig, 1, -5).amount).toBe(300);
  });
});
