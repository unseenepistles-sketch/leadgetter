import { Controller, Get, Inject, NotFoundException, Param, Query } from '@nestjs/common';
import type { ConfigProvider } from '@moveon/shared';
import { classesBySize } from '@moveon/shared';
import { expandBundle, fromCatalogBasket } from '@moveon/pricing';
import { CONFIG_PROVIDER } from './config.provider.js';

/**
 * What the customer app searches when it asks "what are you moving?".
 * Never "how much does it weigh?".
 */
@Controller()
export class CatalogController {
  constructor(@Inject(CONFIG_PROVIDER) private readonly config: ConfigProvider) {}

  @Get('vehicle-classes')
  vehicleClasses(@Query('all') all?: string) {
    return { classes: classesBySize(this.config, { onlyEnabled: all !== 'true' }) };
  }

  @Get('catalog/items')
  items(@Query('q') query?: string, @Query('category') category?: string) {
    const term = query?.trim().toLowerCase();
    const items = this.config.catalog().filter((item) => {
      if (category && item.category !== category) return false;
      if (!term) return true;
      return (
        item.name.toLowerCase().includes(term) ||
        item.aliases.some((alias) => alias.toLowerCase().includes(term))
      );
    });
    return { items, categories: [...new Set(this.config.catalog().map((i) => i.category))] };
  }

  @Get('catalog/bundles')
  bundles() {
    return { bundles: this.config.bundles() };
  }

  /** One tap loads a whole basket, already summed. */
  @Get('catalog/bundles/:id')
  bundle(@Param('id') id: string) {
    const bundle = this.config.bundles().find((b) => b.id === id);
    if (!bundle) throw new NotFoundException({ error: 'unknown_bundle', bundleId: id });
    const lines = expandBundle(this.config, id);
    return { bundle, lines, profile: fromCatalogBasket(this.config, lines) };
  }
}
