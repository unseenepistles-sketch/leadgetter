import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import 'reflect-metadata';
import { Test } from '@nestjs/testing';
import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { AppModule } from './app.module.js';
import { PricingExceptionFilter } from './errors.filter.js';

let app: INestApplication;

beforeAll(async () => {
  const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
  app = moduleRef.createNestApplication();
  app.useGlobalFilters(new PricingExceptionFilter());
  await app.init();
});

afterAll(async () => { await app.close(); });

const houseMove = {
  route: { distanceKm: 23.4, durationInTrafficMin: 48 },
  load: {
    method: 'catalog',
    lines: [
      { itemId: 'sofa_5seater', quantity: 1 }, { itemId: 'bed_frame_headboard', quantity: 2 },
      { itemId: 'wardrobe_2door', quantity: 2 }, { itemId: 'dining_set_6', quantity: 1 },
      { itemId: 'carton_medium', quantity: 15 },
    ],
  },
  helpers: 1, pickupFloors: 2,
};

describe('catalog', () => {
  it('serves the vehicle classes ops has switched on', async () => {
    const response = await request(app.getHttpServer()).get('/vehicle-classes').expect(200);
    expect(response.body.classes.map((c: any) => c.code)).toEqual(['pickup_single', 'canter_3t']);
  });

  it('serves every class when ops asks', async () => {
    const response = await request(app.getHttpServer()).get('/vehicle-classes?all=true').expect(200);
    expect(response.body.classes).toHaveLength(9);
  });

  it('finds items by local name, not just English', async () => {
    const response = await request(app.getHttpServer()).get('/catalog/items?q=mabati').expect(200);
    expect(response.body.items.map((i: any) => i.id)).toEqual(['iron_sheets_10']);
  });

  it('filters by category', async () => {
    const response = await request(app.getHttpServer()).get('/catalog/items?category=Appliances').expect(200);
    expect(response.body.items.length).toBeGreaterThan(0);
    expect(response.body.items.every((i: any) => i.category === 'Appliances')).toBe(true);
  });

  it('serves the whole catalog unfiltered', async () => {
    const response = await request(app.getHttpServer()).get('/catalog/items').expect(200);
    expect(response.body.items).toHaveLength(40);
    expect(response.body.categories.length).toBeGreaterThan(3);
  });

  it('expands a bundle into a priced-ready basket in one tap', async () => {
    const response = await request(app.getHttpServer()).get('/catalog/bundles/house_move_1br').expect(200);
    expect(response.body.profile.volumeM3).toBeGreaterThan(0);
    expect(response.body.lines.length).toBeGreaterThan(5);
  });

  it('404s an unknown bundle', async () => {
    await request(app.getHttpServer()).get('/catalog/bundles/nope').expect(404);
  });

  it('lists the bundles', async () => {
    const response = await request(app.getHttpServer()).get('/catalog/bundles').expect(200);
    expect(response.body.bundles.length).toBe(9);
  });
});

describe('quoting', () => {
  it('turns a basket into a firm price', async () => {
    const response = await request(app.getHttpServer()).post('/quotes').send(houseMove).expect(201);
    expect(response.body.vehicleClass).toBe('canter_3t');
    expect(response.body.total).toBeGreaterThan(0);
    expect(response.body.currency).toBe('KES');
    expect(response.body.breakdown.some((l: any) => l.code === 'curbside' && l.amount === 0)).toBe(true);
  });

  it('reads a quote back by id', async () => {
    const created = await request(app.getHttpServer()).post('/quotes').send(houseMove).expect(201);
    const fetched = await request(app.getHttpServer()).get(`/quotes/${created.body.quoteId}`).expect(200);
    expect(fetched.body.total).toBe(created.body.total);
  });

  it('404s an unknown quote', async () => {
    await request(app.getHttpServer()).get('/quotes/q_nope').expect(404);
  });

  it('rejects a malformed request with the offending field', async () => {
    const response = await request(app.getHttpServer())
      .post('/quotes').send({ route: { distanceKm: -1, durationInTrafficMin: 5 }, load: { method: 'catalog', lines: [] } })
      .expect(400);
    expect(response.body.error).toBe('invalid_request');
    expect(response.body.details.length).toBeGreaterThan(0);
  });

  it('rejects an unknown catalog item as a client error, not a crash', async () => {
    const response = await request(app.getHttpServer())
      .post('/quotes').send({ route: { distanceKm: 5, durationInTrafficMin: 10 }, load: { method: 'catalog', lines: [{ itemId: 'unicorn', quantity: 1 }] } })
      .expect(400);
    expect(response.body).toMatchObject({ error: 'unknown_catalog_item', itemId: 'unicorn' });
  });

  it('explains a load nothing available can carry', async () => {
    const response = await request(app.getHttpServer())
      .post('/quotes').send({ route: { distanceKm: 5, durationInTrafficMin: 10 }, load: { method: 'catalog', lines: [{ itemId: 'ppr_pipes_10', quantity: 1 }] } })
      .expect(422);
    expect(response.body.error).toBe('no_suitable_vehicle');
    expect(response.body.reasons.join(' ')).toMatch(/bed of 6 m/);
  });
});

describe('the pickup flow (§4 layer 4)', () => {
  const book = async () => (await request(app.getHttpServer()).post('/quotes').send(houseMove).expect(201)).body;

  it('absorbs a slightly bigger load without changing the price', async () => {
    const quote = await book();
    const response = await request(app.getHttpServer())
      .post(`/trips/${quote.quoteId}/load-confirmation`).send({ volumeM3: quote.meta.volumeM3 * 1.1, weightKg: 800 })
      .expect(201);
    expect(response.body.result.outcome).toBe('absorbed');
    expect(response.body.requote).toBeUndefined();
  });

  it('confirms a matching load', async () => {
    const quote = await book();
    const response = await request(app.getHttpServer())
      .post(`/trips/${quote.quoteId}/load-confirmation`).send({ volumeM3: quote.meta.volumeM3, weightKg: quote.meta.weightKg })
      .expect(201);
    expect(response.body.result.outcome).toBe('matches');
  });

  it('re-quotes in-app when the load genuinely will not fit', async () => {
    const quote = await book();
    const response = await request(app.getHttpServer())
      .post(`/trips/${quote.quoteId}/load-confirmation`).send({ volumeM3: 30, weightKg: 2000 })
      .expect(201);
    expect(response.body.result.outcome).toBe('requote_required');
    expect(response.body.requote.deadRunFeeIfCancelled).toBeGreaterThan(0);
  });

  it('404s a confirmation against an unknown quote', async () => {
    await request(app.getHttpServer()).post('/trips/q_nope/load-confirmation').send({ volumeM3: 1, weightKg: 1 }).expect(404);
  });

  it('serves the countdown both apps display', async () => {
    const response = await request(app.getHttpServer()).post('/trips/handling-window').send({ vehicleClass: 'canter_3t' }).expect(201);
    expect(response.body).toEqual({ freeMinutes: 30, graceMinutes: 3, meterStartsAfterMinutes: 33 });
  });

  it('meters waiting only beyond the included window', async () => {
    const inside = await request(app.getHttpServer()).post('/trips/waiting').send({ vehicleClass: 'canter_3t', elapsedMinutesOnSite: 20 }).expect(201);
    expect(inside.body.amount).toBe(0);
    const beyond = await request(app.getHttpServer()).post('/trips/waiting').send({ vehicleClass: 'canter_3t', elapsedMinutesOnSite: 60, overrunsAlreadyThisMonth: 3 }).expect(201);
    expect(beyond.body.amount).toBeGreaterThan(0);
  });

  it('prices a stairs upsell the customer taps to approve', async () => {
    const response = await request(app.getHttpServer()).post('/trips/helper-upsell').send({ helpers: 1, floors: 3 }).expect(201);
    expect(response.body).toMatchObject({ amount: 750, helpers: 1 });
  });

  it('rejects an unknown vehicle class', async () => {
    const response = await request(app.getHttpServer()).post('/trips/waiting').send({ vehicleClass: 'sled', elapsedMinutesOnSite: 60 }).expect(400);
    expect(response.body.error).toBe('unknown_vehicle_class');
  });
});

describe('ops', () => {
  it('reports what is switched on', async () => {
    const response = await request(app.getHttpServer()).get('/ops/health').expect(200);
    expect(response.body).toMatchObject({ status: 'ok', catalogItems: 40, bundles: 9 });
  });

  it('serves the rate card the ops console edits', async () => {
    const response = await request(app.getHttpServer()).get('/ops/pricing-config').expect(200);
    expect(response.body.pricing.currency).toBe('KES');
    expect(response.body.classPricing).toHaveLength(9);
  });
});
