import { Module } from '@nestjs/common';
import { CatalogController } from './catalog.controller.js';
import { QuotesController } from './quotes.controller.js';
import { TripsController } from './trips.controller.js';
import { OpsController } from './ops.controller.js';
import { CONFIG_PROVIDER, SeedConfigProvider } from './config.provider.js';
import { QuoteStore } from './quote.store.js';
import { Clock } from './clock.js';

@Module({
  controllers: [CatalogController, QuotesController, TripsController, OpsController],
  providers: [
    { provide: CONFIG_PROVIDER, useFactory: () => new SeedConfigProvider() },
    QuoteStore,
    Clock,
  ],
})
export class AppModule {}
