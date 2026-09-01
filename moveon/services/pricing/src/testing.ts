import { StaticConfigProvider, type ConfigProvider } from '@moveon/shared';
import type { ParsedQuoteRequest, QuoteRequest } from '@moveon/shared';
import { quoteRequestSchema, defaultConfig } from '@moveon/shared';

/** Test-only helpers. Excluded from coverage — they are scaffolding, not product. */

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

export interface ConfigPatch {
  pricing?: (pricing: any) => void;
  vehicleClasses?: (classes: any[]) => void;
  classPricing?: (rows: any[]) => void;
  catalog?: (items: any[]) => void;
  bundles?: (bundles: any[]) => void;
}

/** A validated config built by mutating a copy of the shipped seed data. */
export function configWith(patch: ConfigPatch): ConfigProvider {
  const pricing = clone(defaultConfig.pricing());
  const vehicleClasses = clone(defaultConfig.vehicleClasses()) as any[];
  const classPricing = clone(defaultConfig.classPricing()) as any[];
  const catalog = clone(defaultConfig.catalog()) as any[];
  const bundles = clone(defaultConfig.bundles()) as any[];

  patch.pricing?.(pricing);
  patch.vehicleClasses?.(vehicleClasses);
  patch.classPricing?.(classPricing);
  patch.catalog?.(catalog);
  patch.bundles?.(bundles);

  return new StaticConfigProvider({ pricing, vehicleClasses, classPricing, catalog, bundles });
}

/** Enables exactly the named classes, leaving everything else off. */
export function configWithClasses(...codes: string[]): ConfigProvider {
  return configWith({ vehicleClasses: (classes) => classes.forEach((c) => { c.enabled = codes.includes(c.code); }) });
}

/**
 * A provider that skips coherence validation, for exercising defensive paths a
 * valid configuration can never reach (such as having no vehicles at all).
 */
export function bareProvider(overrides: Partial<Record<keyof ConfigProvider, unknown>> = {}): ConfigProvider {
  return {
    vehicleClasses: () => (overrides.vehicleClasses as any) ?? defaultConfig.vehicleClasses(),
    classPricing: () => (overrides.classPricing as any) ?? defaultConfig.classPricing(),
    pricing: () => (overrides.pricing as any) ?? defaultConfig.pricing(),
    catalog: () => (overrides.catalog as any) ?? defaultConfig.catalog(),
    bundles: () => (overrides.bundles as any) ?? defaultConfig.bundles(),
  };
}

export const CTX = { now: new Date('2026-03-04T10:00:00.000Z'), quoteId: 'q_test' };

/** A short CBD run with a few boxes, unless overridden. */
export function makeRequest(overrides: Partial<QuoteRequest> = {}): ParsedQuoteRequest {
  return quoteRequestSchema.parse({
    route: { distanceKm: 10, durationInTrafficMin: 20 },
    load: { method: 'catalog', lines: [{ itemId: 'carton_medium', quantity: 4 }] },
    ...overrides,
  });
}

/** Catalog basket shorthand. */
export function basket(...lines: [string, number][]): QuoteRequest['load'] {
  return { method: 'catalog', lines: lines.map(([itemId, quantity]) => ({ itemId, quantity })) };
}

export function lineAmount(breakdown: { code: string; amount: number }[], code: string): number | undefined {
  return breakdown.find((l) => l.code === code)?.amount;
}
