"""The daily reminder run: scan, claim, send.

Three phases, each independently restartable:

1. **Scan** — walk every active employee against every entitled item and work out
   which stage, if any, applies today. Pure computation, no writes.
2. **Claim** — insert a ledger row per reminder before anything is sent. A
   duplicate key means it already went out; skip it silently.
3. **Send** — group claimed reminders into one digest per recipient, send, then
   mark them. Picks up stragglers left pending by an earlier crashed run.

Guards exist because the failure that ends an engagement is switching this on
against years of history and mailing the entire workforce in one morning: a hard
per-run cap, a start-date cutoff, dry-run by default, and a recipient allowlist
outside production.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from ..domain.models import ItemStatus
from ..service import UniformService
from . import stages as stage_defs
from .ledger import Claim, ReminderLedger, dedupe_key
from .mailer import ConsoleMailer, Mailer, Message, apply_allowlist

log = logging.getLogger("uniforms.reminders")


@dataclass
class RunResult:
    scanned: int = 0
    claimed: int = 0
    retried: int = 0
    skipped_already_sent: int = 0
    suppressed: int = 0
    messages: int = 0
    sent: int = 0
    failed: int = 0
    capped: bool = False
    dry_run: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "claimed": self.claimed,
            "retried": self.retried,
            "skipped_already_sent": self.skipped_already_sent,
            "suppressed": self.suppressed,
            "messages": self.messages,
            "sent": self.sent,
            "failed": self.failed,
            "capped": self.capped,
            "dry_run": self.dry_run,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    status: ItemStatus
    stage: stage_defs.Stage
    recipient: str
    key: str


class ReminderRunner:
    def __init__(
        self,
        service: UniformService,
        ledger: ReminderLedger,
        mailer: Optional[Mailer] = None,
        *,
        stores_email: str = "",
        allowlist: Sequence[str] = (),
        max_sends_per_run: int = 200,
        start_date: Optional[date] = None,
        brand: str = "Uniform Manager",
    ) -> None:
        self.service = service
        self.ledger = ledger
        self.mailer = mailer or ConsoleMailer()
        self.stores_email = stores_email
        self.allowlist = list(allowlist)
        self.max_sends_per_run = max_sends_per_run
        self.start_date = start_date
        self.brand = brand

    # ------------------------------------------------------------------- scan

    def scan(self, today: Optional[date] = None) -> list[_Candidate]:
        today = today or date.today()
        due_soon = self.service.policy.due_soon_days
        employees = self.service.snapshot.employees
        found: list[_Candidate] = []

        for status in self.service.all_statuses(today):
            stage = stage_defs.stage_for(status, due_soon_days=due_soon)
            if stage is None:
                continue
            emp = employees.get(status.employee_number)
            if emp is None:
                continue
            for recipient in self._recipients(stage, emp):
                found.append(
                    _Candidate(
                        status=status,
                        stage=stage,
                        recipient=recipient,
                        key=dedupe_key(
                            status.employee_number, status.item_code, status.next_due,
                            stage.code, recipient,
                        ),
                    )
                )
        return found

    def _recipients(self, stage: stage_defs.Stage, employee) -> list[str]:
        out: list[str] = []
        for who in stage.audience:
            if who == stage_defs.EMPLOYEE and employee.email:
                out.append(employee.email)
            elif who == stage_defs.MANAGER and employee.manager_email:
                out.append(employee.manager_email)
            elif who == stage_defs.STORES and self.stores_email:
                out.append(self.stores_email)
        return list(dict.fromkeys(out))  # de-dup, keep order

    # ------------------------------------------------------------------- run

    def run(self, today: Optional[date] = None, *, dry_run: bool = True) -> RunResult:
        today = today or date.today()
        result = RunResult(dry_run=dry_run)

        candidates = self.scan(today)
        result.scanned = len(candidates)

        if dry_run:
            claimed = self._preview(candidates, result)
        else:
            claimed = self._claim(candidates, result)

        if result.suppressed:
            result.notes.append(
                f"{result.suppressed} reminder(s) suppressed: due before the "
                f"{self.start_date} cutoff"
            )

        # Phase 3: one digest per recipient. Five items due is one email, not five.
        groups = self._group(claimed)
        result.messages = len(groups)

        if len(groups) > self.max_sends_per_run:
            result.capped = True
            result.notes.append(
                f"capped at {self.max_sends_per_run} of {len(groups)} messages — "
                "raise MAX_SENDS_PER_RUN once the volume is understood"
            )
            groups = dict(list(groups.items())[: self.max_sends_per_run])

        for recipient, items in groups.items():
            keys = [c.key for c in items]
            message = self._compose(recipient, items)
            if dry_run:
                ConsoleMailer().send(message)
                result.sent += 1
                continue
            target = apply_allowlist(recipient, self.allowlist)
            if target is None:
                continue
            try:
                self.mailer.send(Message(target, message.subject, message.body))
                self.ledger.mark_sent(keys)
                result.sent += 1
            except Exception as exc:  # noqa: BLE001 - recorded, retried next run
                log.exception("failed sending to %s", target)
                self.ledger.mark_failed(keys, str(exc))
                result.failed += 1

        if dry_run:
            result.notes.append("dry run — nothing was sent and nothing was marked sent")
        return result

    def run_locked(self, today: Optional[date] = None, *, dry_run: bool = True) -> Optional[RunResult]:
        """Run once per day across every worker, or not at all."""
        today = today or date.today()
        if not self.ledger.start_run("reminders", today):
            log.info("reminder run for %s already claimed by another worker", today)
            return None
        result = self.run(today, dry_run=dry_run)
        self.ledger.finish_run(
            "reminders", today,
            claimed=result.claimed, sent=result.sent, failed=result.failed,
            note="; ".join(result.notes)[:500],
        )
        return result

    def _preview(self, candidates: Sequence[_Candidate], result: RunResult) -> list[_Candidate]:
        """Work out what a real run would do, writing nothing.

        A dry run must not claim: dry-run is the default, so a nightly scheduled
        preview would otherwise pile up claims that all fire the moment sending is
        switched on — precisely the mass-email event the guards exist to prevent.
        """
        would_send: list[_Candidate] = []
        for cand in candidates:
            if self._before_cutoff(cand):
                result.suppressed += 1
            elif self.ledger.already_handled(cand.key):
                result.skipped_already_sent += 1
            else:
                would_send.append(cand)
        return would_send

    def _claim(self, candidates: Sequence[_Candidate], result: RunResult) -> list[_Candidate]:
        """Reserve every reminder before a single message goes out."""
        claimed: list[_Candidate] = []
        contested: list[_Candidate] = []
        for cand in candidates:
            if self._before_cutoff(cand):
                if self.ledger.claim(self._claim_of(cand), status="suppressed"):
                    result.suppressed += 1
                continue
            if self.ledger.claim(self._claim_of(cand)):
                claimed.append(cand)
                result.claimed += 1
            else:
                contested.append(cand)

        # A claim we did not win is only "already sent" if it actually was. Rows
        # left pending by a crashed or failed run are retried here — otherwise the
        # claim that protects against duplicates would also guarantee a permanent
        # loss the first time SMTP hiccups.
        retryable = self.ledger.pending_among([c.key for c in contested])
        for cand in contested:
            if cand.key in retryable:
                claimed.append(cand)
                result.retried += 1
            else:
                result.skipped_already_sent += 1
        return claimed

    # --------------------------------------------------------------- helpers

    def _claim_of(self, cand: _Candidate) -> Claim:
        return Claim(
            dedupe_key=cand.key,
            employee_number=cand.status.employee_number,
            item_code=cand.status.item_code,
            due_date=cand.status.next_due,
            stage=cand.stage.code,
            recipient=cand.recipient,
        )

    def _before_cutoff(self, cand: _Candidate) -> bool:
        if self.start_date is None or cand.status.next_due is None:
            return False
        return cand.status.next_due < self.start_date

    def _group(self, claimed: Sequence[_Candidate]) -> dict[str, list[_Candidate]]:
        grouped: dict[str, list[_Candidate]] = defaultdict(list)
        for cand in claimed:
            grouped[cand.recipient].append(cand)
        return dict(grouped)

    def _compose(self, recipient: str, items: Sequence[_Candidate]) -> Message:
        by_employee: dict[str, list[_Candidate]] = defaultdict(list)
        for cand in items:
            by_employee[cand.status.employee_name].append(cand)

        lines: list[str] = []
        for name, rows in sorted(by_employee.items()):
            lines.append(name)
            for cand in sorted(rows, key=lambda c: c.status.item_name):
                due = cand.status.next_due.strftime("%d %b %Y") if cand.status.next_due else "—"
                lines.append(
                    f"  - {cand.status.item_name} (x{cand.status.quantity})"
                    f" — {cand.stage.label.lower()}, due {due}"
                )
            lines.append("")

        count = len(items)
        if len(by_employee) == 1 and count == 1:
            subject = f"{self.brand}: {items[0].status.item_name} {items[0].stage.blurb}"
        else:
            subject = f"{self.brand}: {count} uniform item(s) need attention"

        body = (
            "The following uniform items need attention:\n\n"
            + "\n".join(lines).rstrip()
            + "\n\nThis is an automated reminder.\n"
        )
        return Message(recipient, subject, body)
