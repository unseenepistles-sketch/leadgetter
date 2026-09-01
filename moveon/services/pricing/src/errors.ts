/** Typed failures the API layer maps onto HTTP codes. Never throw bare strings at money code. */
export class PricingError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = 'PricingError';
  }
}

export class UnknownCatalogItemError extends PricingError {
  constructor(readonly itemId: string) {
    super('unknown_catalog_item', `No catalog item with id "${itemId}"`);
  }
}

export class UnknownVehicleClassError extends PricingError {
  constructor(readonly vehicleClass: string) {
    super('unknown_vehicle_class', `No vehicle class "${vehicleClass}" is configured`);
  }
}

export class NoSuitableVehicleError extends PricingError {
  constructor(readonly reasons: string[]) {
    super('no_suitable_vehicle', `No available vehicle can take this load: ${reasons.join('; ')}`);
  }
}
