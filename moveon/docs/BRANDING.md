# Name check (§14) — do this before the truck decals

The brief asked for this to be settled before writing code. Here is the check,
and what the codebase actually does about it.

## 1. `.on` is not a top-level domain — verified

Checked against the IANA root zone (Feb 2026 listing, ~1,593 delegated TLDs).
`.on` is **not delegated**, and there is no plausible path to it: two-letter TLDs
are reserved for ISO 3166-1 country codes, and `ON` is not one. It is Ontario's
*third*-level Canadian domain, `on.ca`.

So `move.on` cannot ever be an address. The stylised dot is a logo device, and
every customer who types it into a browser lands nowhere. In a market where a
large share of traffic arrives by someone reading a name off the side of a
vehicle and typing it in, that is a real, recurring cost — not a cosmetic one.

**Practical options:** `moveon.co.ke` (right for a Kenyan launch, and `.co.ke`
carries local trust), or a `.com` if you ever want a regional story. Whatever you
pick, the wordmark should render the address, not the dot device, anywhere a
customer might type it — vehicle livery above all.

## 2. MoveOn.org owns the search term

MoveOn (moveon.org) is a large, long-established US political advocacy
organisation with a heavy media footprint. You will not outrank it for "move on",
and you do not want to: brand-adjacent search results that are US partisan
politics are a strange first impression for a Kenyan logistics company, and an
active liability if the brand ever travels.

This is a marketing-cost problem, not a legal one — different sector, different
market, so trademark collision is unlikely. But it means paid search on your own
brand name forever.

## 3. The semantics work against the promise

"Move on" means *get over it, leave it behind.* The entire product promise is
**arriving with your things intact.** The name quietly says the opposite of the
thing customers are anxious about.

## Recommendation

The first two are costs you can absorb. The third is the one that would keep me
up: a cargo brand's single job is to signal *your things will arrive*, and this
name signals abandonment. A Swahili-rooted name would do more work in market —
something built on *beba* (carry) or *sogeza* (shift/move something) reads as
local, means the right thing, and has an available `.co.ke`.

This is the founder's call, not the engineer's. What the code does about it:

## What the codebase assumes

Nothing. The name appears only in the package scope `@moveon/*`, the directory
name, and prose. There is **no brand string in any pricing, catalog, schema, or
API contract** — no `moveon` literal in a database column, an enum, a JSON key, or
a URL path. Renaming is a find-and-replace across package manifests plus a
directory move; it will not touch a single business rule.

Deliberately so. Given §14 flags the name as unresolved, the build should not
harden a decision the brief itself says is still open.
