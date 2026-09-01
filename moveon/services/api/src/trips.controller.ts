import { Body, Controller, Inject, NotFoundException, Param, Post } from '@nestjs/common';
import { z } from 'zod';
import { confirmLoad, buildRequote, priceWaiting, priceHelperUpsell, handlingWindow } from '@moveon/pricing';
import { CONFIG_PROVIDER, type ConfigProvider } from './config.provider.js';
import { ZodValidationPipe } from './zod.pipe.js';
import { QuoteStore } from './quote.store.js';
import { Clock } from './clock.js';

const loadConfirmationSchema = z.object({
  volumeM3: z.number().nonnegative(),
  weightKg: z.number().nonnegative(),
  minBedLengthM: z.number().nonnegative().optional(),
  requiresEnclosed: z.boolean().optional(),
  passengers: z.number().int().min(0).default(0),
  driverRisk: z.object({ overReportRate: z.number().min(0).max(1), observations: z.number().int().min(0) }).optional(),
});

const waitingSchema = z.object({
  vehicleClass: z.string().min(1),
  elapsedMinutesOnSite: z.number().nonnegative(),
  overrunsAlreadyThisMonth: z.number().int().min(0).default(0),
});

const helperUpsellSchema = z.object({
  helpers: z.number().int(),
  floors: z.number().int(),
});

@Controller('trips')
export class TripsController {
  constructor(
    @Inject(CONFIG_PROVIDER) private readonly config: ConfigProvider,
    @Inject(QuoteStore) private readonly store: QuoteStore,
    @Inject(Clock) private readonly clock: Clock,
  ) {}

  /**
   * Layer 4 (§4). The driver taps one of three outcomes at pickup and this
   * decides which it was — from the numbers, not from either party's opinion.
   * A re-quote comes back as a price the customer approves in-app.
   */
  @Post(':quoteId/load-confirmation')
  confirm(
    @Param('quoteId') quoteId: string,
    @Body(new ZodValidationPipe(loadConfirmationSchema)) body: z.infer<typeof loadConfirmationSchema>,
  ) {
    const stored = this.store.get(quoteId);
    if (!stored) throw new NotFoundException({ error: 'unknown_quote', quoteId });

    const actual = {
      volumeM3: body.volumeM3,
      weightKg: body.weightKg,
      minBedLengthM: body.minBedLengthM,
      requiresEnclosed: body.requiresEnclosed,
    };
    const result = confirmLoad(this.config, stored.quote, actual, {
      passengers: body.passengers,
      driverRisk: body.driverRisk,
    });

    if (result.outcome !== 'requote_required') return { result };

    const requote = buildRequote(this.config, stored.quote, stored.request, actual, {
      now: this.clock.now(),
      quoteId: this.clock.newId('q'),
    });
    this.store.save(requote.revised, stored.request);
    return { result, requote };
  }

  /** The countdown both apps show, so nobody is surprised by the meter. */
  @Post('handling-window')
  window(@Body(new ZodValidationPipe(z.object({ vehicleClass: z.string().min(1) }))) body: { vehicleClass: string }) {
    return handlingWindow(this.config, body.vehicleClass);
  }

  @Post('waiting')
  waiting(@Body(new ZodValidationPipe(waitingSchema)) body: z.infer<typeof waitingSchema>) {
    return priceWaiting(this.config, body.vehicleClass, body.elapsedMinutesOnSite, body.overrunsAlreadyThisMonth);
  }

  /** Stairs found at pickup become a tap-to-approve upsell, not a pavement argument. */
  @Post('helper-upsell')
  helperUpsell(@Body(new ZodValidationPipe(helperUpsellSchema)) body: z.infer<typeof helperUpsellSchema>) {
    return priceHelperUpsell(this.config, body.helpers, body.floors);
  }
}
