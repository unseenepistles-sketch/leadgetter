# Build order (§11) — what is done, what is not

The brief is emphatic about sequence: *"Milestone 1 — The engine, headless. If
the pricing is wrong, nothing else matters."* That is what this drop is.

## Milestone 1 — the engine, headless ✅ built

- Vehicle classes and rate cards as ops-editable config, with a validator that
  rejects the mistakes someone will actually make (a rate card that rises instead
  of tapering, a bundle pointing at a deleted item, every vehicle switched off).
- A 40-item catalog of things people in this market actually move, with the two
  edge cases the brief calls out flagged in the schema: `densityCritical`
  (cement, blocks, ballast, tiles) and `lengthCritical` (mabati, timber, pipes,
  with `minBedLengthM`). Nine one-tap bundles.
- Load estimation layers 1–3 (catalog basket, fill slider, photo estimate) all
  normalising to one load profile. Layer 4 — the driver tolerance flow — with the
  absorb/re-quote decision and both anti-gaming scores.
- The pricing engine: tapered distance, time in traffic, load factor on the
  binding constraint, capped surge, the helper add-on, backhaul/scheduled/business
  discounts with a stacking cap.
- **196 tests, 100% branch/function/line/statement coverage** on the pricing
  service, enforced in `vitest.config.ts` — §13.5 is a gate, not an aspiration.
- A headless NestJS API over all of it (24 integration tests) and a
  Postgres + PostGIS schema with an idempotent seed, verified against a real
  database.

## Milestone 2 — customer booking flow ⬜ not built

Expo app: phone + OTP auth, address entry, item basket, vehicle recommendation,
quote screen, booking. The API already serves catalog search, bundle expansion
and quoting; this is the client plus a real `RoutingProvider` (Google Directions
or Mapbox — `duration_in_traffic` is not optional, the time component depends on
it) behind an interface, because you will switch when the bill arrives.

## Milestone 3 — driver app + dispatch ⬜ not built

Go online, receive job, accept, navigate, confirm load, complete. Redis geo
indexing for live positions; matching on
`proximity x vehicle_fit x rating x acceptance x idle_time`, with vehicle fit as
a **hard filter** — `assessFit()` in `services/pricing` is already that function.
Broadcast to top N with a short accept window, expand radius on timeout, and show
the driver the whole job before they accept.

## Milestone 4 — money ⬜ not built

M-Pesa Daraja: STK Push for charges, B2C for payouts. The callback flow is
asynchronous and times out often on poor networks — poll or socket, never assume.
Cash is mandatory here and creates a driver-owes-platform balance, so the wallet
needs its negative-balance floor enforced before a driver can go online. The
ledger tables are already in `prisma/schema.prisma`: driver earnings are **read**
from double-entry rows, never recomputed from trip records.

## Milestone 5 — trust ⬜ not built

Driver onboarding gates (ID, licence, logbook, insurance, tax PIN, police
clearance, selfie liveness — tables exist), proof of pickup and delivery with
recipient OTP, shareable live tracking link, SOS, disputes, ops console.

## Milestone 6 — margin ⬜ not built

Backhaul matching as a first-class service — empty return legs are ~40% of
trucking cost and the discount rate is already wired into the engine, it just has
nothing matching loads to corridors yet. Then scheduled capacity planning and
business accounts.

## Deliberately not built (§12)

Not oversights — cuts. All nine vehicle classes at launch (seeded, but only
`pickup_single` and `canter_3t` are enabled), multi-city, passenger ride-hailing,
in-app chat, loyalty/gamification, iOS-first, fleet route optimisation,
blockchain anything.

## Acceptance criteria (§13)

| # | Criterion | Status |
|---|---|---|
| 1 | Accurate quote in <45s with no knowledge of weight or volume | engine + API ready; needs the Milestone 2 UI to measure |
| 2 | Quoted price = charged price in >=95% of trips | tolerance band designed for it; needs live trips to measure |
| 3 | Driver sees full job before accepting | Milestone 3 |
| 4 | Curbside loading included, visibly, with a live countdown | priced and served (`/trips/handling-window`); needs the app UI |
| 5 | Pricing service 100% branch coverage | ✅ enforced in CI config |
| 6 | Whole flow works on 3G / 2GB Android | Milestone 2–3 |
