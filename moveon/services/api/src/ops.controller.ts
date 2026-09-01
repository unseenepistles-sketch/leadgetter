import { Controller, Get, Inject } from '@nestjs/common';
import { CONFIG_PROVIDER, type ConfigProvider } from './config.provider.js';

/** What the ops console reads today, and will write to in Milestone 5. */
@Controller('ops')
export class OpsController {
  constructor(@Inject(CONFIG_PROVIDER) private readonly config: ConfigProvider) {}

  @Get('pricing-config')
  pricingConfig() {
    return { pricing: this.config.pricing(), classPricing: this.config.classPricing() };
  }

  @Get('health')
  health() {
    return {
      status: 'ok',
      vehicleClassesEnabled: this.config.vehicleClasses().filter((c) => c.enabled).map((c) => c.code),
      catalogItems: this.config.catalog().length,
      bundles: this.config.bundles().length,
    };
  }
}
