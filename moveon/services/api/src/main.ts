import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module.js';
import { PricingExceptionFilter } from './errors.filter.js';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  app.useGlobalFilters(new PricingExceptionFilter());
  // Cheap Android on 3G (§2.6): keep payloads small and the surface flat.
  app.enableCors();
  const port = Number(process.env.PORT ?? 3000);
  await app.listen(port);
  // eslint-disable-next-line no-console
  console.log(`move.on api listening on :${port}`);
}

void bootstrap();
