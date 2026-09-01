import { z } from 'zod';
import {
  vehicleClassSchema,
  catalogItemSchema,
  catalogBundleSchema,
  type VehicleClass,
  type CatalogItem,
  type CatalogBundle,
  type VehicleClassCode,
} from './schemas.js';
import { classPricingSchema, pricingConfigSchema, type ClassPricing, type PricingConfig } from './config-schema.js';

import vehicleClassesJson from './config/vehicle-classes.json';
import classPricingJson from './config/class-pricing.json';
import pricingJson from './config/pricing.json';
import catalogJson from './config/catalog.json';
import bundlesJson from './config/bundles.json';

/**
 * Everything the pricing engine reads comes through this interface. The seed
 * implementation reads the JSON in `src/config`; in production ops edits the
 * same shapes in Postgres and a DB-backed provider is dropped in here. The
 * engine never knows the difference, and rates change without a deploy (§3).
 */
export interface ConfigProvider {
  vehicleClasses(): readonly VehicleClass[];
  classPricing(): readonly ClassPricing[];
  pricing(): PricingConfig;
  catalog(): readonly CatalogItem[];
  bundles(): readonly CatalogBundle[];
}

export class ConfigError extends Error {
  constructor(what: string, cause: z.ZodError) {
    super(`Invalid ${what} config: ${cause.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`);
    this.name = 'ConfigError';
  }
}

function parse<T>(what: string, schema: z.ZodType<T, z.ZodTypeDef, unknown>, raw: unknown): T {
  const result = schema.safeParse(raw);
  if (!result.success) throw new ConfigError(what, result.error);
  return result.data;
}

export interface ConfigInput {
  vehicleClasses?: unknown;
  classPricing?: unknown;
  pricing?: unknown;
  catalog?: unknown;
  bundles?: unknown;
}

/**
 * Validates once at construction. A malformed rate table should fail on boot or
 * on an ops save — never halfway through quoting a customer.
 */
export class StaticConfigProvider implements ConfigProvider {
  private readonly _vehicleClasses: readonly VehicleClass[];
  private readonly _classPricing: readonly ClassPricing[];
  private readonly _pricing: PricingConfig;
  private readonly _catalog: readonly CatalogItem[];
  private readonly _bundles: readonly CatalogBundle[];

  constructor(input: ConfigInput = {}) {
    this._vehicleClasses = parse('vehicle classes', z.array(vehicleClassSchema).min(1), input.vehicleClasses ?? vehicleClassesJson);
    this._classPricing = parse('class pricing', z.array(classPricingSchema).min(1), input.classPricing ?? classPricingJson);
    this._pricing = parse('pricing', pricingConfigSchema, input.pricing ?? pricingJson);
    this._catalog = parse('catalog', z.array(catalogItemSchema), input.catalog ?? catalogJson);
    this._bundles = parse('bundles', z.array(catalogBundleSchema), input.bundles ?? bundlesJson);
    assertConfigIsCoherent(this);
  }

  vehicleClasses(): readonly VehicleClass[] { return this._vehicleClasses; }
  classPricing(): readonly ClassPricing[] { return this._classPricing; }
  pricing(): PricingConfig { return this._pricing; }
  catalog(): readonly CatalogItem[] { return this._catalog; }
  bundles(): readonly CatalogBundle[] { return this._bundles; }
}

/**
 * Cross-file invariants zod can't express on its own. These are the mistakes an
 * ops user will actually make in the pricing screen at 11pm.
 */
export function assertConfigIsCoherent(config: ConfigProvider): void {
  const problems: string[] = [];
  const classes = config.vehicleClasses();
  const codes = new Set(classes.map((c) => c.code));

  if (codes.size !== classes.length) problems.push('duplicate vehicle class codes');

  for (const code of codes) {
    if (!config.classPricing().some((p) => p.vehicleClass === code)) problems.push(`no pricing row for vehicle class "${code}"`);
  }
  for (const row of config.classPricing()) {
    if (!codes.has(row.vehicleClass)) problems.push(`pricing row for unknown vehicle class "${row.vehicleClass}"`);
    // Bands must ascend and terminate open-ended, or long hauls silently lose a segment.
    let previous = 0;
    row.distanceBands.forEach((band, index) => {
      const isLast = index === row.distanceBands.length - 1;
      if (band.uptoKm === null && !isLast) problems.push(`${row.vehicleClass}: open-ended distance band must be last`);
      if (isLast && band.uptoKm !== null) problems.push(`${row.vehicleClass}: last distance band must be open-ended (uptoKm: null)`);
      if (band.uptoKm !== null) {
        if (band.uptoKm <= previous) problems.push(`${row.vehicleClass}: distance bands must ascend (${band.label})`);
        previous = band.uptoKm;
      }
    });
    // A taper that goes up is the 3x-the-market bug the brief warns about.
    for (let i = 1; i < row.distanceBands.length; i += 1) {
      const prior = row.distanceBands[i - 1]!;
      const current = row.distanceBands[i]!;
      if (current.ratePerKm > prior.ratePerKm) problems.push(`${row.vehicleClass}: distance rate must taper, not rise (${current.label})`);
    }
  }

  const loadBands = config.pricing().loadFactor.bands;
  for (let i = 1; i < loadBands.length; i += 1) {
    if (loadBands[i]!.upToFraction <= loadBands[i - 1]!.upToFraction) problems.push('load factor bands must ascend');
  }
  if (loadBands[loadBands.length - 1]!.upToFraction < 1) problems.push('load factor bands must cover a full vehicle');

  const itemIds = new Set(config.catalog().map((i) => i.id));
  if (itemIds.size !== config.catalog().length) problems.push('duplicate catalog item ids');
  for (const item of config.catalog()) {
    if (item.lengthCritical && item.minBedLengthM === undefined) problems.push(`catalog item "${item.id}" is length-critical but has no minBedLengthM`);
  }
  for (const bundle of config.bundles()) {
    for (const line of bundle.items) {
      if (!itemIds.has(line.itemId)) problems.push(`bundle "${bundle.id}" references unknown item "${line.itemId}"`);
    }
  }

  if (!classes.some((c) => c.enabled)) problems.push('no vehicle class is enabled — nothing can be dispatched');

  if (problems.length > 0) throw new Error(`Incoherent move.on config:\n  - ${problems.join('\n  - ')}`);
}

/** Convenience for callers that just want the shipped seed configuration. */
export const defaultConfig: ConfigProvider = new StaticConfigProvider();

export function findVehicleClass(config: ConfigProvider, code: VehicleClassCode): VehicleClass | undefined {
  return config.vehicleClasses().find((c) => c.code === code);
}

export function findClassPricing(config: ConfigProvider, code: VehicleClassCode): ClassPricing | undefined {
  return config.classPricing().find((p) => p.vehicleClass === code);
}

/** Smallest first — the order vehicle recommendation walks. */
export function classesBySize(config: ConfigProvider, options: { onlyEnabled?: boolean } = {}): VehicleClass[] {
  const onlyEnabled = options.onlyEnabled ?? true;
  return config
    .vehicleClasses()
    .filter((c) => (onlyEnabled ? c.enabled : true))
    .slice()
    .sort((a, b) => a.volumeM3 - b.volumeM3 || a.payloadKg - b.payloadKg || a.code.localeCompare(b.code));
}
