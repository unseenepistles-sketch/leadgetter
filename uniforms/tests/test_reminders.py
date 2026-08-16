from __future__ import annotations

from datetime import date

import pytest

from app.domain.due import Policy
from app.excelstore.workbook import WorkbookStore
from app.reminders.ledger import PENDING, SENT, Claim, ReminderLedger, dedupe_key
from app.reminders.mailer import ConsoleMailer, Message, apply_allowlist
from app.reminders.runner import ReminderRunner
from app.reminders.stages import DUE, NEVER_ISSUED, OVERDUE_30, T1M, T6M, stage_for
from app.service import UniformService
from tests.fixtures import clean_workbook


@pytest.fixture
def service(tmp_path):
    store = WorkbookStore(clean_workbook(tmp_path / "w.xlsx"), backup_dir=tmp_path / "bak")
    store.load()
    return UniformService(store, Policy())


@pytest.fixture
def ledger(tmp_path):
    return ReminderLedger(tmp_path / "ledger.sqlite3")


@pytest.fixture
def runner(service, ledger):
    return ReminderRunner(
        service, ledger, ConsoleMailer(), stores_email="stores@co.test", max_sends_per_run=100
    )


# --- stage selection walks an item through its life ---

def _status(days_until, name="s"):
    from app.domain.models import ItemStatus, UniformStatus
    return ItemStatus(
        employee_number="E1", employee_name="X", item_code="SHIRT", item_name="Shirt",
        quantity=1, status=UniformStatus.OK, cycle_months=12,
        next_due=date(2025, 1, 1), days_until_due=days_until,
    )


@pytest.mark.parametrize(
    "days,expected",
    [(200, None), (180, T6M), (31, T6M), (30, T1M), (1, T1M), (0, DUE), (-29, DUE), (-30, OVERDUE_30)],
)
def test_stage_boundaries(days, expected):
    assert stage_for(_status(days)) is expected


def test_never_issued_has_its_own_stage(service):
    statuses = service.statuses_for("E002", today=date(2024, 6, 1))
    blazer = next(s for s in statuses if s.item_code == "BLAZER")
    assert stage_for(blazer) is NEVER_ISSUED


def test_needs_review_never_produces_a_stage():
    from app.domain.models import ItemStatus, UniformStatus
    bad = ItemStatus("E1", "X", "SHIRT", "Shirt", 1, UniformStatus.NEEDS_REVIEW, 12)
    assert stage_for(bad) is None


# --- the ledger is the idempotency guarantee ---

def test_claim_succeeds_once_and_only_once(ledger):
    claim = Claim("k1", "E001", "SHIRT", date(2025, 1, 15), "T6M", "a@co.test")
    assert ledger.claim(claim) is True
    assert ledger.claim(claim) is False


def test_dedupe_key_includes_the_due_date_so_next_cycle_sends_again():
    a = dedupe_key("E001", "SHIRT", date(2025, 1, 15), "T1M")
    b = dedupe_key("E001", "SHIRT", date(2026, 1, 15), "T1M")
    assert a != b


def test_each_stage_is_claimed_separately():
    keys = {dedupe_key("E001", "SHIRT", date(2025, 1, 15), s) for s in ("T6M", "T1M", "DUE")}
    assert len(keys) == 3


def test_pending_rows_are_picked_up_and_marked(ledger):
    ledger.claim(Claim("k1", "E001", "SHIRT", date(2025, 1, 15), "T6M", "a@co.test"))
    assert [r["dedupe_key"] for r in ledger.pending()] == ["k1"]
    ledger.mark_sent(["k1"])
    assert ledger.pending() == []
    assert ledger.counts()[SENT] == 1


def test_failures_retry_then_quarantine(ledger):
    ledger.claim(Claim("k1", "E001", "SHIRT", date(2025, 1, 15), "T6M", "a@co.test"))
    for _ in range(2):
        ledger.mark_failed(["k1"], "smtp down", max_attempts=3)
        assert ledger.pending()  # still retryable
    ledger.mark_failed(["k1"], "smtp down", max_attempts=3)
    assert ledger.pending() == []  # quarantined, not looping forever


def test_job_lock_admits_one_worker_per_day(ledger):
    assert ledger.start_run("reminders", date(2024, 6, 1)) is True
    assert ledger.start_run("reminders", date(2024, 6, 1)) is False
    assert ledger.start_run("reminders", date(2024, 6, 2)) is True


# --- running ---

def test_dry_run_writes_nothing_to_the_ledger(runner, ledger):
    """A preview must leave no trace — see test_repeated_dry_runs_do_not_pile_up."""
    result = runner.run(date(2024, 8, 1), dry_run=True)
    assert result.dry_run and result.scanned > 0
    assert result.sent > 0  # it reports what it *would* send
    assert ledger.counts() == {}
    assert ledger.pending() == []


def test_repeated_dry_runs_do_not_pile_up_claims(runner, ledger):
    """Dry run is the default, so a nightly preview must not accumulate work
    that all fires the moment live sending is switched on."""
    for _ in range(3):
        runner.run(date(2024, 8, 1), dry_run=True)
    assert ledger.counts() == {}

    live = runner.run(date(2024, 8, 1), dry_run=False)
    assert live.claimed == live.scanned
    assert live.retried == 0


def test_dry_run_does_not_re_report_what_was_already_sent(runner):
    runner.run(date(2024, 8, 1), dry_run=False)
    preview = runner.run(date(2024, 8, 1), dry_run=True)
    assert preview.sent == 0
    assert preview.skipped_already_sent > 0


def test_live_run_sends_and_marks(runner, ledger):
    result = runner.run(date(2024, 8, 1), dry_run=False)
    assert result.sent > 0
    assert ledger.pending() == []
    assert ledger.counts()[SENT] == result.claimed


def test_running_twice_sends_nothing_the_second_time(runner):
    first = runner.run(date(2024, 8, 1), dry_run=False)
    second = runner.run(date(2024, 8, 1), dry_run=False)
    assert first.claimed > 0
    assert second.claimed == 0
    assert second.sent == 0
    assert second.skipped_already_sent == first.claimed


def test_a_crash_between_claim_and_send_loses_nothing(service, ledger):
    """Simulate the mailer dying: rows stay pending and the next run picks them up."""
    class Broken:
        def send(self, message): raise RuntimeError("smtp down")

    crashed = ReminderRunner(service, ledger, Broken(), stores_email="stores@co.test")
    first = crashed.run(date(2024, 8, 1), dry_run=False)
    assert first.failed > 0 and first.sent == 0
    assert ledger.pending()  # nothing was silently dropped

    good = ConsoleMailer()
    recovered = ReminderRunner(service, ledger, good, stores_email="stores@co.test")
    second = recovered.run(date(2024, 8, 1), dry_run=False)

    # The pending rows are retried, not written off as "already sent".
    assert second.retried == first.claimed
    assert second.claimed == 0
    assert second.sent > 0
    assert good.sent, "the recovered run must actually deliver"
    assert ledger.pending() == []
    assert ledger.counts()[SENT] == first.claimed


def test_a_third_run_after_recovery_sends_nothing(service, ledger):
    """Recovery must not become a licence to re-send forever."""
    runner = ReminderRunner(service, ledger, ConsoleMailer(), stores_email="stores@co.test")
    runner.run(date(2024, 8, 1), dry_run=False)
    mailer = ConsoleMailer()
    again = ReminderRunner(service, ledger, mailer, stores_email="stores@co.test")
    result = again.run(date(2024, 8, 1), dry_run=False)
    assert result.sent == 0
    assert mailer.sent == []


def test_stores_and_employee_both_get_the_six_month_notice(service, ledger):
    """One stage with two audiences must produce two emails, not one."""
    mailer = ConsoleMailer()
    runner = ReminderRunner(service, ledger, mailer, stores_email="stores@co.test")
    runner.run(date(2024, 8, 1), dry_run=False)
    recipients = {m.to for m in mailer.sent}
    assert "stores@co.test" in recipients
    assert any(r != "stores@co.test" for r in recipients)


def test_recipients_get_one_digest_not_one_email_per_item(runner):
    result = runner.run(date(2024, 8, 1), dry_run=False)
    assert result.claimed > result.messages


def test_digest_lists_every_item(service, ledger):
    mailer = ConsoleMailer()
    runner = ReminderRunner(service, ledger, mailer, stores_email="stores@co.test")
    runner.run(date(2024, 8, 1), dry_run=False)
    stores = [m for m in mailer.sent if m.to == "stores@co.test"]
    assert stores
    assert "Blazer" in stores[0].body or "Shirt" in stores[0].body


def test_left_employees_are_not_reminded(runner):
    """E004 has status Left and must never appear."""
    candidates = runner.scan(date(2024, 8, 1))
    assert all(c.status.employee_number != "E004" for c in candidates)


def test_start_date_cutoff_suppresses_the_backfill(service, ledger):
    """The go-live guard: old due dates are claimed as suppressed, never sent."""
    mailer = ConsoleMailer()
    runner = ReminderRunner(
        service, ledger, mailer, stores_email="stores@co.test",
        start_date=date(2024, 8, 1),
    )
    result = runner.run(date(2024, 8, 1), dry_run=False)
    assert result.suppressed > 0
    for message in mailer.sent:
        assert "never been issued" not in message.subject


def test_send_cap_limits_the_blast_radius(service, ledger):
    runner = ReminderRunner(
        service, ledger, ConsoleMailer(), stores_email="stores@co.test", max_sends_per_run=1
    )
    result = runner.run(date(2024, 8, 1), dry_run=False)
    assert result.capped
    assert result.sent <= 1
    assert any("capped" in n for n in result.notes)


def test_run_locked_only_runs_once_per_day(runner):
    assert runner.run_locked(date(2024, 8, 1), dry_run=True) is not None
    assert runner.run_locked(date(2024, 8, 1), dry_run=True) is None


# --- allowlist keeps non-production quiet ---

def test_allowlist_rewrites_unknown_recipients():
    assert apply_allowlist("real@company.com", ["dev@us.test"]) == "dev@us.test"


def test_allowlist_passes_through_listed_addresses():
    assert apply_allowlist("dev@us.test", ["dev@us.test"]) == "dev@us.test"


def test_no_allowlist_means_send_as_addressed():
    assert apply_allowlist("real@company.com", []) == "real@company.com"
