from __future__ import annotations

from datetime import date

import pytest

from app.domain.due import (
    Policy,
    add_months,
    compute_status,
    latest_issuance_by_item,
    statuses_for_employee,
)
from app.domain.models import (
    Employee,
    Entitlement,
    Issuance,
    UniformItem,
    UniformStatus,
)

SHIRT = UniformItem("SHIRT", "Shirt", "shirt", renewal_cycle_months=12, size_key="shirt")
BLAZER = UniformItem("BLAZER", "Blazer", "blazer", renewal_cycle_months=24, size_key="blazer")
ITEMS = {i.item_code: i for i in (SHIRT, BLAZER)}

ENT_SHIRT = Entitlement("officer", "SHIRT", 3)
ENT_BLAZER = Entitlement("officer", "BLAZER", 1)


def emp(**kw) -> Employee:
    base = dict(
        employee_number="E1",
        full_name="Sam Okoro",
        role="officer",
        join_date=date(2020, 1, 15),
        sizes={"shirt": "L", "blazer": "42R"},
    )
    base.update(kw)
    return Employee(**base)


def iss(item: str, when: date, **kw) -> Issuance:
    return Issuance(employee_number="E1", item_code=item, issued_date=when, **kw)


# --- add_months: the month-end clamping that timedelta(days=365) gets wrong ---

@pytest.mark.parametrize(
    "start,months,expected",
    [
        (date(2024, 2, 29), 12, date(2025, 2, 28)),  # leap day -> non-leap year
        (date(2024, 1, 31), 1, date(2024, 2, 29)),   # clamp to end of Feb
        (date(2023, 1, 31), 1, date(2023, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 4, 30)),
        (date(2024, 1, 15), 12, date(2025, 1, 15)),  # ordinary case
        (date(2024, 1, 15), 24, date(2026, 1, 15)),
        (date(2024, 12, 31), 1, date(2025, 1, 31)),  # year rollover
        (date(2024, 6, 10), 0, date(2024, 6, 10)),
    ],
)
def test_add_months_clamps_to_month_end(start, months, expected):
    assert add_months(start, months) == expected


def test_add_months_never_drifts_across_leap_years():
    """Four annual renewals from a fixed day land on that day, not 1-2 days early."""
    d = date(2020, 3, 10)
    for _ in range(4):
        d = add_months(d, 12)
    assert d == date(2024, 3, 10)


# --- the 12 vs 24 month split ---

def test_shirt_uses_12_month_cycle():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 6, 1))
    assert s.next_due == date(2025, 1, 15)
    assert s.cycle_months == 12


def test_blazer_uses_24_month_cycle():
    s = compute_status(emp(), BLAZER, ENT_BLAZER, iss("BLAZER", date(2024, 1, 15)), date(2024, 6, 1))
    assert s.next_due == date(2026, 1, 15)
    assert s.cycle_months == 24


def test_cycle_snapshot_on_issuance_wins_over_current_catalog():
    """Policy changed to 18 months, but this row was issued under the old 12."""
    changed = UniformItem("SHIRT", "Shirt", renewal_cycle_months=18)
    s = compute_status(
        emp(), changed, ENT_SHIRT,
        iss("SHIRT", date(2024, 1, 15), cycle_months=12),
        date(2024, 6, 1),
    )
    assert s.cycle_months == 12
    assert s.next_due == date(2025, 1, 15)


# --- classification boundaries ---

def test_ok_when_renewal_is_far_off():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 3, 1))
    assert s.status is UniformStatus.OK


def test_due_soon_exactly_at_the_six_month_trigger():
    """due 2025-01-15; 180 days before is 2024-07-19."""
    on_trigger = compute_status(
        emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 7, 19)
    )
    assert on_trigger.status is UniformStatus.DUE_SOON
    assert on_trigger.days_until_due == 180

    day_before = compute_status(
        emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 7, 18)
    )
    assert day_before.status is UniformStatus.OK


def test_due_on_the_due_date_itself():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2025, 1, 15))
    assert s.status is UniformStatus.DUE
    assert s.days_until_due == 0


def test_still_due_inside_the_overdue_grace_window():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2025, 2, 14))
    assert s.status is UniformStatus.DUE


def test_overdue_once_grace_has_passed():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2025, 2, 15))
    assert s.status is UniformStatus.OVERDUE
    assert s.days_until_due == -31


# --- the "missed" case: no issuance row exists anywhere ---

def test_never_issued_after_the_joining_grace_period():
    s = compute_status(emp(join_date=date(2020, 1, 15)), SHIRT, ENT_SHIRT, None, date(2024, 6, 1))
    assert s.status is UniformStatus.NEVER_ISSUED
    assert s.last_issued is None
    assert s.is_outstanding


def test_new_starter_inside_grace_is_due_not_missed():
    s = compute_status(emp(join_date=date(2024, 5, 20)), SHIRT, ENT_SHIRT, None, date(2024, 6, 1))
    assert s.status is UniformStatus.DUE


def test_never_issued_is_invisible_to_the_issuance_log():
    """The reason the engine walks entitlements rather than scanning issuances."""
    rows = [iss("SHIRT", date(2024, 1, 15))]
    statuses = statuses_for_employee(
        emp(), [ENT_SHIRT, ENT_BLAZER], ITEMS, rows, date(2024, 6, 1)
    )
    by_item = {s.item_code: s for s in statuses}
    assert by_item["BLAZER"].status is UniformStatus.NEVER_ISSUED
    assert by_item["SHIRT"].status is UniformStatus.OK


# --- bad data must never produce a confident reminder ---

def test_missing_join_date_and_no_issuance_needs_review():
    s = compute_status(emp(join_date=None), SHIRT, ENT_SHIRT, None, date(2024, 6, 1))
    assert s.status is UniformStatus.NEEDS_REVIEW
    assert not s.is_outstanding


def test_missing_join_date_is_fine_once_something_was_issued():
    s = compute_status(
        emp(join_date=None), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 6, 1)
    )
    assert s.status is UniformStatus.OK


def test_parser_flagged_row_needs_review():
    s = compute_status(
        emp(problems=("unparseable join date '13/13/2020'",)),
        SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 6, 1),
    )
    assert s.status is UniformStatus.NEEDS_REVIEW
    assert "13/13/2020" in s.reason


# --- picking the right issuance out of a history ---

def test_latest_issuance_wins_regardless_of_sheet_order():
    rows = [
        iss("SHIRT", date(2022, 1, 15), row=2),
        iss("SHIRT", date(2024, 1, 15), row=3),
        iss("SHIRT", date(2023, 1, 15), row=4),
    ]
    assert latest_issuance_by_item(rows)["SHIRT"].issued_date == date(2024, 1, 15)


def test_same_day_duplicate_breaks_the_tie_on_row_order():
    rows = [
        iss("SHIRT", date(2024, 1, 15), row=2, size="L"),
        iss("SHIRT", date(2024, 1, 15), row=7, size="XL"),
    ]
    assert latest_issuance_by_item(rows)["SHIRT"].size == "XL"


# --- sizes and quantities feed the issue form ---

def test_size_prefills_from_the_employee_profile():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, None, date(2024, 6, 1))
    assert s.size == "L"


def test_previously_issued_size_beats_the_profile_default():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15), size="M"), date(2024, 6, 1))
    assert s.size == "M"


def test_entitlement_quantity_is_carried_through():
    s = compute_status(emp(), SHIRT, ENT_SHIRT, None, date(2024, 6, 1))
    assert s.quantity == 3


def test_inactive_items_are_skipped():
    items = {"SHIRT": SHIRT, "BLAZER": UniformItem("BLAZER", "Blazer", active=False)}
    statuses = statuses_for_employee(emp(), [ENT_SHIRT, ENT_BLAZER], items, [], date(2024, 6, 1))
    assert [s.item_code for s in statuses] == ["SHIRT"]


def test_policy_thresholds_are_configurable():
    lenient = Policy(due_soon_days=30)
    s = compute_status(
        emp(), SHIRT, ENT_SHIRT, iss("SHIRT", date(2024, 1, 15)), date(2024, 7, 19), lenient
    )
    assert s.status is UniformStatus.OK
