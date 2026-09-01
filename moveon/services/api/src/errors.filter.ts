import { Catch, HttpException, HttpStatus, Logger, type ArgumentsHost, type ExceptionFilter } from '@nestjs/common';
import type { Response } from 'express';
import { PricingError, NoSuitableVehicleError, UnknownCatalogItemError, UnknownVehicleClassError } from '@moveon/pricing';

/**
 * Typed pricing failures become useful HTTP, so the apps can show a customer
 * something better than "something went wrong".
 */
@Catch()
export class PricingExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger('api');

  catch(exception: unknown, host: ArgumentsHost): void {
    const response = host.switchToHttp().getResponse<Response>();

    if (exception instanceof HttpException) {
      response.status(exception.getStatus()).json(exception.getResponse());
      return;
    }

    if (exception instanceof PricingError) {
      response.status(statusFor(exception)).json({ error: exception.code, message: exception.message, ...detail(exception) });
      return;
    }

    // Anything reaching here is a bug, not a business outcome. Never silent.
    this.logger.error('Unhandled exception', exception instanceof Error ? exception.stack : String(exception));
    response.status(HttpStatus.INTERNAL_SERVER_ERROR).json({ error: 'internal_error' });
  }
}

function statusFor(error: PricingError): number {
  // A load nothing can carry is a real, expected answer — not a server fault.
  if (error instanceof NoSuitableVehicleError) return HttpStatus.UNPROCESSABLE_ENTITY;
  if (error instanceof UnknownCatalogItemError || error instanceof UnknownVehicleClassError) return HttpStatus.BAD_REQUEST;
  return HttpStatus.INTERNAL_SERVER_ERROR;
}

function detail(error: PricingError): Record<string, unknown> {
  if (error instanceof NoSuitableVehicleError) return { reasons: error.reasons };
  if (error instanceof UnknownCatalogItemError) return { itemId: error.itemId };
  if (error instanceof UnknownVehicleClassError) return { vehicleClass: error.vehicleClass };
  return {};
}
