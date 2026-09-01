# move.on

A two-sided cargo marketplace that **never asks the customer what their load
weighs.** It asks what they're moving, converts that into vehicle space, and
prices the vehicle. Cargo is measured in how much of a vehicle it fills.

Every "Uber for cargo" dies on the same rock: the customer can't describe the
load, so the price is a guess, so the driver argues at pickup, so trust dies.
Everything here is built around removing that argument.

Built for an East African launch (KES, M-Pesa, boda/tuktuk/canter taxonomy).
Swap the payment rail and the vehicle table for another market — nothing else
changes, because both are config.

## What's in this drop

**Milestone 1: the engine, headless.** The brief's own instruction — *"If the
pricing is wrong, nothing else matters."* Catalog, vehicle classes, the pricing
service and its full test suite, plus a runnable API and database schema over the
top. The three apps (customer, driver, ops) are Milestones 2–5 and are **not**
built. See [docs/ROADMAP.md](docs/ROADMAP.md) for exactly what is and isn't here.

```
packages/shared     types, zod schemas, ops-editable config + catalog seed
services/pricing    the pure engine — no IO, 100% branch coverage
services/api        NestJS over the engine: catalog, quotes, pickup flow
prisma/             Postgres + PostGIS schema and seed
docs/               PRICING.md, ROADMAP.md, BRANDING.md
```

## Run it

```bash
pnpm install
pnpm test                # 242 tests across the workspace
pnpm test:coverage       # pricing service, 100% branches enforced
pnpm api:dev             # headless API on :3000
```

No database is needed for any of that — Milestone 1 is deliberately storage-free.
To seed Postgres:

```bash
docker compose up -d postgres     # postgis/postgis:16-3.4
cp .env.example .env
pnpm db:push && pnpm seed
```

## Try it

```bash
# What are you moving? (searches local names too — mabati, mitumba, debe)
curl 'localhost:3000/catalog/items?q=mabati'

# One tap for a whole house
curl localhost:3000/catalog/bundles/house_move_2br

# A firm price, before booking
curl -X POST localhost:3000/quotes -H 'content-type: application/json' -d '{
  "route": { "distanceKm": 23.4, "durationInTrafficMin": 48 },
  "load": { "method": "catalog", "lines": [
    { "itemId": "sofa_5seater", "quantity": 1 },
    { "itemId": "bed_frame_headboard", "quantity": 2 },
    { "itemId": "carton_medium", "quantity": 15 } ] },
  "helpers": 1, "pickupFloors": 2 }'
```

```jsonc
{
  "vehicleClass": "canter_3t",
  "recommendedReason": "Your 18 items need ~5.2 m³ — that's a Canter 3t",
  "total": 4710, "currency": "KES",
  "breakdown": [
    { "label": "Base fare", "amount": 800 },
    { "label": "Distance (23.4 km)", "amount": 2351 },
    { "label": "Est. time in traffic (48 min)", "amount": 960 },
    { "label": "Load factor (43% full)", "amount": 0 },
    { "label": "Helper x1 (2 floors)", "amount": 600 },
    { "label": "Curbside loading & unloading (30 min incl.)", "amount": 0 }
  ],
  "included": [
    "Curbside loading & unloading (30 min included)",
    "Goods cover up to KES 50,000",
    "Live tracking you can share with the receiver"
  ]
}
```

## The rules this encodes

These are product rules in code, not aspirations in a doc:

1. **No weighing scales, ever.** No flow asks for kilograms. Weight exists only as
   a legal ceiling the *system* checks on the customer's behalf.
2. **The price shown is the price paid** — unless the load is materially wrong, and
   then the system re-quotes in-app. Two humans never negotiate at the roadside.
3. **Curbside loading is included, and the labour is funded.** Free window per
   class, visible countdown, metered overruns, paid helpers for stairs.
4. **Distance alone is a naive price.** Time in traffic is half the driver's cost
   and is priced. A 6 km CBD run at 5pm is not a 6 km highway run.
5. **Driver hourly earnings are the north star.** Nothing here quietly transfers
   cost onto the driver.
6. **Fit is a hard filter, not a score.** A pickup is never dispatched to a 9 m³
   load.

Where the brief and honest engineering disagreed, [docs/PRICING.md](docs/PRICING.md)
says so and explains the call — the loading-is-free clause (§5) and load-factor
banding are the two that mattered.

## Naming

`move.on` has a problem the brief itself flags: `.on` is not a delegated TLD and
cannot become one. That check is done, with the other two objections, in
[docs/BRANDING.md](docs/BRANDING.md). No brand string appears in any pricing
rule, schema column, enum or API path, so renaming stays a find-and-replace.
