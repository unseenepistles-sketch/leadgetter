# Uniform Management System

Tracks which uniforms each employee has been issued, works out what is due, shows who was
missed, and sends reminder emails — with **their existing Excel workbook as the database**.

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

### The one thing that is not in their workbook

The record of **which reminders have already been sent** lives in a small SQLite file the app
owns. It needs a real uniqueness guarantee: if that state sat in a spreadsheet, someone
re-sorting or deleting rows would re-email ten thousand people. Everything the client thinks of
as *their data* stays in Excel.

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
reminders — never guessed at, and never turned into a confidently wrong email.

## Statuses

| Status | Meaning |
|---|---|
| `never_issued` | Entitled to it, no issuance row exists at all — **the "who was missed" case** |
| `overdue` | Past due by more than the grace period |
| `due` | Due now |
| `due_soon` | Within 6 months of the next renewal |
| `ok` | Up to date |
| `needs_review` | Data too poor to judge. Suppressed from reminders. |

`never_issued` is why the engine walks *employees × entitlements* rather than scanning the
issuance log: someone never given a blazer has no row anywhere, so no query over issuances could
ever find them.

## Reminders

Daily job. Each stage fires once, then the item moves to the next as time passes.

| Stage | When | To |
|---|---|---|
| `T6M` | 6 months before due | **Stores** + employee — this is a procurement lead-time signal |
| `T1M` | 1 month before due | Employee |
| `DUE` | on the due date | Employee |
| `OVERDUE_30` | 30 days past due | Employee + manager |
| `NEVER_ISSUED` | after the joining grace period | Stores + manager |

Recipients get **one digest each** — five items due is one email, not five.

**Idempotency**: every reminder is claimed in the ledger, keyed on
`employee|item|due_date|stage|recipient`, *before* the message is sent. A crash or retry
therefore causes a *missed* email (visible as pending, re-sent next run) rather than a duplicate.
The due date is in the key, so next cycle's reminder correctly fires again.

### Go-live guards

Switching this on against years of history would mail the entire workforce in one morning. So:

* `REMINDERS_ENABLED=false` and `REMINDERS_DRY_RUN=true` by **default** — sending is opt-in.
* A dry run writes **nothing** to the ledger, so nightly previews cannot pile up claims that all
  fire the moment you go live.
* `REMINDERS_START_DATE` pre-suppresses everything due before go-live.
* `MAX_SENDS_PER_RUN` caps the blast radius.
* `RECIPIENT_ALLOWLIST` rewrites every address outside production.

Roll out to one department for a fortnight before going company-wide.

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

**Run a single worker.** The scheduler is in-process and the workbook has one writer; extra
uvicorn workers would mean several schedulers. The daily run also takes a per-day lock in the
ledger as a second line of defence.

## Pages

| Page | |
|---|---|
| `/` | Counts for never-issued, overdue, due, due-soon; workbook and reminder health |
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
POST /api/reminders/run?dry_run=true
GET  /api/reminders
POST /api/workbook/reload        POST /api/workbook/flush
```

All list endpoints paginate. Pages and API are peers — both call the service layer directly,
neither goes through the other, so a different front end could be added without touching any
business logic.

## Tests

```bash
python -m pytest
```

145 tests. The due-date engine is pure and exhaustively covered (12- vs 24-month cycles,
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
