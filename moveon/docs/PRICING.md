# The pricing engine

`services/pricing` is a pure function. Input: a quote request. Output: a quote
object. No database writes, no network calls, no clock reads, no randomness
inside the calculation — `now` and the quote id are passed in by the caller.

That is why the API supplies them (`services/api/src/clock.ts`) instead of the
engine reaching for `new Date()`. It is also why the whole thing can be tested to
100% on branches without a single mock.

## The formula

```
core        = base_fare + distance(tapered) + time_in_traffic
load_factor = core x (multiplier - 1)
subtotal    = core + load_factor

+ night surcharge        subtotal x rate         (22:00-05:00, window wraps midnight)
+ demand                 subtotal x (capped - 1) (cap is config; uncapped surge is a PR grenade)
+ return leg             subtotal x rate
+ additional stops       flat fee x stops
+ fragile handling       flat fee
+ enclosed vehicle       flat fee
+ helpers                helpers x (call-out + floors x per-floor)
+ curbside handling      0                        (shown, so nobody wonders)

- discounts              gross x rate, scaled together if they exceed the cap

total = round(max(net, minimum_fare x trips), rounding_step)
```

Every number in that formula is config (`packages/shared/src/config/`), not a
literal. Ops changes rates without a deploy.

## Distance tapers

Marginal, like tax brackets — the first 5 km bill at the top rate, the next 15 at
the second, and so on. Flat per-km makes you 3x the market on long hauls and
nobody books an upcountry run twice. The config validator **rejects a rate card
whose bands rise instead of taper**, because that is the bug that matters.

## Load factor bands on the binding constraint, not on space

The brief bands on fill fraction. That is right until you load two tonnes of
cement into a canter: 12% of the bed, 67% of the legal payload. Banding on volume
would price that at x1.00 and hand the driver a job they cannot double up on for
the price of a near-empty run.

So the band is chosen on `max(volumeFraction, weightFraction)` — whichever
actually commits the vehicle. The x1.20 density premium still applies on top when
mass is the binding constraint *and* is at the ceiling. The receipt line names
whichever constraint the customer is really paying for:

```
Load factor (82% full)
Load factor (heavy for its size, 67% of payload)
```

## Vehicle fit is a hard filter

Five constraints, all hard: volume, payload, bed length, enclosure, passenger
seats. Never a weighted score. Dispatching a pickup to a 9 m³ load is the exact
failure mode that kills generalist ride apps entering cargo.

Volume and payload can be solved by splitting a load across trips. Bed length,
enclosure and seats cannot — a 6 m pipe does not become shorter — so those refuse
with a typed error the API renders as `422` plus the reasons in plain words.

## Loading is included, and the labour is funded (§5)

The brief's original "loading is free" is unpriced, not free, and unpriced labour
is paid for in driver churn. So:

- **Curbside handling is genuinely included**, 5–45 min by class, counting down
  visibly in both apps.
- **Beyond the window the meter runs**, per minute, after a grace period. The
  first two overruns a month are waived — a customer whose lift was broken is not
  a problem customer.
- **Stairs are a paid helper add-on**, charged per floor per helper, and a helper
  at ground level still pays a call-out, because they still showed up.

The defensible claim: *"No hidden loading fees. Curbside handling included on
every trip."*

## The tolerance band (§4 layer 4)

At pickup the driver confirms the load. Three outcomes, decided from the numbers
rather than either party's opinion:

| Outcome | When | What happens |
|---|---|---|
| `matches` | at or under declared | proceed |
| `absorbed` | bigger, but the dispatched vehicle can still do it | **no re-quote, no price change** |
| `requote_required` | genuinely will not fit | new price, approved in-app or cancelled |

Absorbing costs a little margin per trip and buys peace at scale — two humans
never negotiate money at the roadside, which is the failure this product exists
to prevent. An overrun past the tolerance is still absorbed if the vehicle can do
it, but it is **logged against the customer** either way. The tolerance is a
courtesy, not a blind spot.

On a re-quote the engine will not propose splitting the load across several
trips. That is a booking-time decision the customer makes; proposing it to a
driver already standing at a pickup is not a re-quote, it is an argument. Those
go to ops.

Both sides are policed by the same rule. A customer over the under-declaration
threshold loses the tolerance band, must use a photo estimate, and prepays. A
driver over the over-reporting threshold has their re-quotes gated behind ops.
Neither is judged on a thin record.

## Alternatives

Every quote carries a cheaper option with an honest warning where one exists —
and *only* where one exists. On the shipped rate card a canter is well under
twice a pickup, so two pickup trips genuinely cost more, and the engine offers no
split alternative rather than inventing one. Trust compounds; a squeezed extra
200 shillings does not.

## Worked example

23.4 km, 48 min in traffic, a 2-bedroom house move, one helper, two floors:

```
   800   Base fare
  2351   Distance (23.4 km)          5km@160 + 15km@88 + 3.4km@68
   960   Est. time in traffic (48 min)
   329   Load factor (82% full)      x1.08
   250   Fragile handling
   600   Helper x1 (2 floors)
     0   Curbside loading & unloading (30 min incl.)
  ----
  5290   KES
```
