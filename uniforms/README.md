# Uniform Management System

Tracks which uniforms each employee has been issued, works out what is due, shows who was
missed, and chases what the tailor has not yet delivered — with **their existing Excel
workbook as the database**.

Employees receive a kit on joining, then renew on staggered cycles: shirts and trousers every
12 months, blazers and other durable items every 24. The cycle lengths live in the spreadsheet,
so the client changes policy by typing a number, not by asking for a deploy.

## How it works

The `.xlsx` is the system of record. Nothing is migrated to a database.

At 10,000+ employees that file holds hundreds of thousands of rows, and openpyxl needs seconds
to parse it, so re-reading it per request is impossible. Instead:

* **Read once, index in memory.** Every query is served from the index; the file on disk stays
  the truth. A background poll notices when someone edits it in Excel and re-reads.
* **One writer.** Recording a handover updates the index immediately — the user sees it at once
  — and queues a row. A single background thread persists it, so concurrent requests can never
  interleave and corrupt the workbook.
* **Rows are spliced straight into the sheet XML** rather than round-tripping the whole file.
  That is the difference between a **15-second** write and a **0.9-second** one at real scale,
  and it leaves the client's formatting, formulas and other sheets untouched.
* **Never write in place** — write to a temp file, then atomically replace. Timestamped backups
  before every flush, most recent 10 kept.

## Sheets it reads

Column headers are matched **by name, not position**, against a list of aliases — so "Staff ID",
"Employee No." and "Payroll Number" all resolve to the same field, and reordering columns is
harmless. Dates are parsed across text formats, real datetimes and Excel serial numbers.

| Sheet | Columns |
|---|---|
| `Employees` | Employee Number, Full Name, Email, Manager Email, Department, Role, Join Date, Status, sizes |
| `UniformItems` | Item Code, Name, Category, **Renewal Cycle Months**, Default Quantity, Active |
| `Entitlements` | Role, Item Code, Quantity — rules *by role*, not one row per employee |
| `Issuances` | Employee Number, Item Code, Issued Date, Quantity, Size, Issued By, Cycle Months |

Tab names are matched loosely too: `Staff List`, `Uniforms`, `Allocations` and `Issue Log` are
all recognised.

Anything unparseable becomes a **visible problem on the Data Quality page** and is excluded from
the renewal figures — never guessed at, and never turned into a confidently
wrong due date.

## Orders

The real sequence: she raises an order against a **PR number**, the tailor visits
to take **measurements**, and the tailor returns with the clothes — sometimes all
of them, sometimes some now and the rest later.

The PR number *is* the order's identity. She is issued one per order and types it
in, so a synthetic id would be one more thing to keep in step with the paperwork.

One PR covers a whole bulk order — thirty-five people and a hundred and sixty-eight
lines is a normal one — so order lines are keyed by **(PR, employee, item)**.
Keying on the PR alone treats the second person on an order as a duplicate.

| Status | Meaning |
|---|---|
| `awaiting_measurement` | Raised, but the tailor has not been to take sizes |
| `pending` | Measured, nothing delivered yet |
| `partially_delivered` | Some of it arrived; a balance is outstanding |
| `delivered` | All of it arrived |
| `cancelled` | — |

Measurement is its own stage because an order can sit there for weeks, and "the
tailor has not been yet" is a different problem from "the tailor has not
delivered". A delivery is refused until the visit is recorded — but a recorded
delivery outranks a missing measurement date, because if the clothes are here the
tailor plainly came.

Two things follow, and both matter:

* **"4 of 6 delivered" is always computed** from real handovers, never a number
  somebody has to remember to update. An order's status comes from its totals by
  the same rule a single line uses.
* **The renewal clock starts at delivery, not at the order.** Ordered in January,
  arrived in June, renews next June. Each part-delivery keeps its own date.

## Editing and administration

Every record can be corrected: staff details, order quantities and dates,
delivery dates, and renewal periods. Two rules hold throughout:

* **Edits find their row by natural key at the moment of writing**, never by a
  remembered row number. The client edits this workbook in Excel, and inserting a
  single row shifts every cached index below it — a keyed edit cannot land on the
  wrong person's record. The issuance sheet has no natural key, so those edits
  carry the values they expect and refuse to write if the row no longer matches.
* **Every changed field is written to a `ChangeLog` sheet** with its previous
  value, who changed it and why. A no-op edit writes nothing.

Edits refuse changes that contradict recorded facts — an order quantity cannot
drop below what has already been delivered.

**Changing a renewal period affects future issues only.** Every issuance snapshots
the period in force when it was made, so moving Shirts to 18 months does not
retroactively shift dates on garments already issued under 12.

### Renewal overrides

A calculated renewal date can be replaced by a manual one, but only with a reason
and an authoriser, both recorded. An override sets the date; it does not excuse
the item — an overridden date in the past still reads as **overdue**, so an
override cannot be used to make a problem disappear from the report.

## Sign-in

Off by default so a local demo needs no setup. **Switch it on before this is
reachable by anyone but you.**

```
AUTH_ENABLED=true
SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
AUTH_USERS=njiru:$(python scripts/hash_password.py 'her password'):admin,desk:...:viewer
```

`admin` may edit; `viewer` may only look. The gate is middleware in front of
everything, so an endpoint added later is protected by default rather than only
if somebody remembers to decorate it.

With sign-in on, **the audit trail is filled in from the session, not from a form
field** — including over the API, where a caller claiming to be someone else is
overruled. That is what turns "authorised by" from a claim into a fact.

This is deliberately small and swappable: the department will most likely end up
signing in with their work accounts once the app is hosted, and the permission
checks are already in the right places for that.

## Statuses

| Status | Meaning |
|---|---|
| `never_issued` | Entitled to it, no issuance row exists at all — **the "who was missed" case** |
| `overdue` | Past due by more than the grace period |
| `due` | Due now |
| `due_soon` | Within 6 months of the next renewal |
| `ok` | Up to date |
| `needs_review` | Data too poor to judge. Shown on the data quality page instead. |

`never_issued` is why the engine walks *employees × entitlements* rather than scanning the
issuance log: someone never given a blazer has no row anywhere, so no query over issuances could
ever find them.

## Chasing what has not arrived

Alerts here are **visual, not email**. Two screens carry them:

* **Renewal alerts** — anything within six months of its renewal date reads amber, anything
  past it reads red. Six months is their procurement lead time, so that is the point at which
  a renewal becomes actionable rather than interesting.
* **Still to come** — every garment ordered and not handed over, longest wait first, with a
  banner once anything passes `CHASE_AFTER_DAYS`. The tailor routinely delivers part of an
  order and says the rest will follow, and *the rest* is what gets forgotten.

An order nobody has been measured for does not appear on that list: the tailor cannot owe you
clothes he has not taken sizes for. Those sit at **awaiting measurement** on the order log.

There is deliberately no mailer. An earlier build had one — staged reminders, an idempotent
send ledger, allowlists and per-run caps — and it was removed rather than left switched off,
because an application that *can* email the whole workforce is a liability when nobody asked
it to.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env

python scripts/make_sample_workbook.py data/uniforms.xlsx    # demo data
uvicorn app.main:app --reload
```

Then open <http://localhost:8000>.

To check it holds up at the client's scale:

```bash
python scripts/make_sample_workbook.py data/big.xlsx --employees 10000
WORKBOOK_PATH=./data/big.xlsx uvicorn app.main:app
```

**Run a single worker.** The workbook has one writer, and the file-change poll is in-process;
extra uvicorn workers would mean several of each, racing each other for the same file.

## Pages

| Page | |
|---|---|
| `/` | Counts for never-issued, overdue, due, due-soon; workbook health |
| `/employees` | Search and filter; outstanding count per person |
| `/employees/{n}` | Entitlements, status, full history, and an **Issue** form with sizes prefilled |
| `/issue` | **Bulk issue** — the same kit to a whole intake in one submit |
| `/reports` | Filter by status and department |
| `/data-quality` | Every row the workbook could not be read cleanly |

## API

```
GET  /api/employees?q=&department=&active_only=&page=
GET  /api/employees/{n}          GET /api/employees/{n}/uniforms
GET  /api/items                  GET /api/entitlements?role=
GET  /api/status?state=overdue|due|due_soon|never_issued|ok&department=
GET  /api/dashboard/summary      GET /api/data-quality
POST /api/issuances              POST /api/issuances/bulk
GET  /api/issuances?employee=&item=
POST /api/workbook/reload        POST /api/workbook/flush
```

All list endpoints paginate. Pages and API are peers — both call the service layer directly,
neither goes through the other, so a different front end could be added without touching any
business logic.

## Tests

```bash
python -m pytest
```

210 tests. The due-date engine is pure and exhaustively covered (12- vs 24-month cycles,
never-issued, month-end clamping — `29 Feb + 12 months` is 28 Feb, which is why
`timedelta(days=365)` is never used). The workbook tests run against a deliberately messy
fixture — renamed and reordered columns, four date formats, blank rows, duplicate keys, junk
values — because a fixture that is too tidy lets bugs through to production.

## Known limits

* If someone has the workbook open in Excel, a write may fail. It is surfaced and retried, and
  queued rows are never dropped.
* A human editing the file while the app runs can still lose edits on the next flush. The mtime
  poll and backups reduce the window; they do not close it. This is inherent to using a
  spreadsheet as a live database, and worth restating to the client.
* Single writer by design — this does not scale horizontally. At the point where that hurts, the
  answer is a real database, and the service layer is where that swap would happen.
