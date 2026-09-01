import { Body, Controller, Get, Inject, NotFoundException, Param, Post } from '@nestjs/common';
import { quoteRequestSchema, type ParsedQuoteRequest, type Quote } from '@moveon/shared';
import { priceQuote } from '@moveon/pricing';
import { CONFIG_PROVIDER, type ConfigProvider } from './config.provider.js';
import { ZodValidationPipe } from './zod.pipe.js';
import { QuoteStore } from './quote.store.js';
import { Clock } from './clock.js';

/**
 * Dependencies are injected by explicit token rather than by reflected
 * parameter type: the TypeScript here is transpiled by esbuild, which does not
 * emit `design:paramtypes`, so implicit constructor injection silently yields
 * undefined. Explicit tokens work under every transpiler.
 */
@Controller('quotes')
export class QuotesController {
  constructor(
    @Inject(CONFIG_PROVIDER) private readonly config: ConfigProvider,
    @Inject(QuoteStore) private readonly store: QuoteStore,
    @Inject(Clock) private readonly clock: Clock,
  ) {}

  /**
   * The 45-second path (§13.1): items in, firm price out. Everything
   * impure — the clock, the id, the customer's history — is supplied here, at
   * the edge, so the engine underneath stays a pure function.
   */
  @Post()
  create(@Body(new ZodValidationPipe(quoteRequestSchema)) request: ParsedQuoteRequest): Quote {
    const quote = priceQuote(this.config, request, {
      now: this.clock.now(),
      quoteId: this.clock.newId('q'),
      // Milestone 4 reads this from the customer's record; a fresh customer has none.
      customerRisk: undefined,
    });
    this.store.save(quote, request);
    return quote;
  }

  @Get(':id')
  get(@Param('id') id: string): Quote {
    const found = this.store.get(id);
    if (!found) throw new NotFoundException({ error: 'unknown_quote', quoteId: id });
    return found.quote;
  }
}
