"""Editing the system of record: what changes, what is refused, what is logged."""
from __future__ import annotations

from datetime import date

import pytest
from openpyxl import load_workbook

from app.domain.models import EmployeeStatus
from app.excelstore.workbook import WorkbookStore
from app.service import NotFound, UniformService, ValidationError
from tests.fixtures import clean_workbook


@pytest.fixture
def service(tmp_path):
    store = WorkbookStore(clean_workbook(tmp_path / "w.xlsx"), backup_dir=tmp_path / "bak")
    store.load()
    return UniformService(store)


def _sheet(service, name):
    wb = load_workbook(service.store.path)
    rows = list(wb[name].iter_rows(values_only=True))
    wb.close()
    return rows


# --- editing employees ---

def test_edit_employee_details(service):
    updated = service.edit_employee("E002", {"full_name": "Amina Y. Bello",
                                             "department": "Stations"}, who="njiru")
    assert updated.full_name == "Amina Y. Bello"
    assert updated.department == "Stations"
    assert service.employee("E002").full_name == "Amina Y. Bello"


def test_edit_employee_reaches_the_workbook(service):
    service.edit_employee("E002", {"full_name": "Amina Y. Bello"}, who="njiru")
    service.store.flush()
    rows = _sheet(service, "Employees")
    header = rows[0]
    row = next(r for r in rows[1:] if r[header.index("Employee Number")] == "E002")
    assert row[header.index("Full Name")] == "Amina Y. Bello"


def test_editing_sizes_updates_the_profile(service):
    updated = service.edit_employee("E002", {"shirt_size": "XL"}, who="njiru")
    assert updated.sizes["shirt"] == "XL"
    # and the next issue picks it up
    issued = service.record_issuance("E002", "SHIRT")
    assert issued.size == "XL"


def test_editing_the_role_changes_what_they_are_entitled_to(service):
    before = {s.item_code for s in service.statuses_for("E002")}
    service.edit_employee("E002", {"role": "clerk"}, who="njiru")
    after = {s.item_code for s in service.statuses_for("E002")}
    assert before != after


def test_editing_a_join_date_moves_the_renewal_clock(service):
    service.edit_employee("E002", {"join_date": "2024-01-15"}, who="njiru")
    assert service.employee("E002").join_date == date(2024, 1, 15)


def test_marking_an_employee_as_left_removes_them_from_reports(service):
    service.edit_employee("E002", {"status": "left"}, who="njiru")
    assert service.employee("E002").status is EmployeeStatus.LEFT
    assert all(s.employee_number != "E002" for s in service.all_statuses())


def test_an_unknown_status_is_refused(service):
    with pytest.raises(ValidationError, match="status must be one of"):
        service.edit_employee("E002", {"status": "on holiday"}, who="njiru")


def test_unknown_fields_are_refused(service):
    with pytest.raises(ValidationError, match="cannot edit"):
        service.edit_employee("E002", {"salary": 1}, who="njiru")


def test_editing_nothing_is_a_no_op(service):
    before = service.employee("E002")
    assert service.edit_employee("E002", {"full_name": before.full_name}, who="njiru") == before
    assert service.store.pending_writes == 0


# --- editing orders ---

def test_edit_order_quantity_and_remarks(service):
    order = service.place_order("E002", {"SHIRT": 2})[0]
    line = service.edit_order(order.order_id, {"quantity": 5, "notes": "size change"},
                              who="njiru")
    assert line.order.quantity == 5 and line.order.notes == "size change"
    assert line.pending == 5


def test_order_quantity_cannot_drop_below_what_arrived(service):
    order = service.place_order("E002", {"SHIRT": 4})[0]
    service.receive_delivery(order.order_id, quantity=3)
    with pytest.raises(ValidationError, match="already delivered"):
        service.edit_order(order.order_id, {"quantity": 2}, who="njiru")


def test_reducing_quantity_to_what_arrived_closes_the_order(service):
    order = service.place_order("E002", {"SHIRT": 4})[0]
    service.receive_delivery(order.order_id, quantity=2)
    line = service.edit_order(order.order_id, {"quantity": 2}, who="njiru")
    assert line.status.value == "delivered" and line.pending == 0


def test_cancelling_an_order(service):
    order = service.place_order("E002", {"SHIRT": 2})[0]
    line = service.edit_order(order.order_id, {"cancelled": True}, who="njiru")
    assert line.status.value == "cancelled"
    assert service.pending_delivery() == 0


# --- editing deliveries ---

def test_edit_a_delivery_date(service):
    order = service.place_order("E002", {"SHIRT": 1})[0]
    issued = service.receive_delivery(order.order_id)
    service.store.flush()
    service.store.load()

    row = service.snapshot.issuances_for("E002")[0].row
    updated = service.edit_delivery("E002", row, {"issued_date": "2026-01-15"}, who="njiru")
    assert updated.issued_date == date(2026, 1, 15)
    # the renewal clock follows the corrected date
    assert service.order_line(order.order_id).next_due == date(2027, 1, 15)


def test_future_delivery_date_is_refused(service):
    service.record_issuance("E002", "SHIRT")
    service.store.flush(); service.store.load()
    row = service.snapshot.issuances_for("E002")[0].row
    with pytest.raises(ValidationError, match="future"):
        service.edit_delivery("E002", row, {"issued_date": "2099-01-01"}, who="njiru")


def test_editing_a_delivery_that_does_not_exist(service):
    with pytest.raises(NotFound):
        service.edit_delivery("E002", 999, {"quantity": 1}, who="njiru")


# --- editing renewal periods ---

def test_edit_a_renewal_period(service):
    updated = service.edit_item("SHIRT", {"renewal_cycle_months": 18}, who="njiru")
    assert updated.renewal_cycle_months == 18


def test_changing_a_cycle_does_not_rewrite_history(service):
    """Existing issues keep the cycle in force when they were made."""
    before = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    service.edit_item("SHIRT", {"renewal_cycle_months": 24}, who="njiru")
    after = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    assert after.next_due == before.next_due
    assert after.cycle_months == 12


def test_a_new_issue_uses_the_new_cycle(service):
    service.edit_item("SHIRT", {"renewal_cycle_months": 24}, who="njiru")
    issued = service.record_issuance("E002", "SHIRT")
    assert issued.cycle_months == 24


def test_a_zero_cycle_is_refused(service):
    with pytest.raises(ValidationError, match="at least 1"):
        service.edit_item("SHIRT", {"renewal_cycle_months": 0}, who="njiru")


# --- renewal overrides ---

def test_override_replaces_the_calculated_date(service):
    calculated = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    service.set_renewal_override("E001", "SHIRT", "2030-06-01",
                                 reason="extended wear trial", authorised_by="njiru")
    after = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    assert after.next_due == date(2030, 6, 1) != calculated.next_due
    assert after.reason == "renewal date set manually"


def test_an_override_in_the_past_still_reads_as_overdue(service):
    """An override sets the date; it does not excuse the item."""
    service.set_renewal_override("E001", "SHIRT", "2020-01-01",
                                 reason="corrected record", authorised_by="njiru")
    after = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    assert after.status.value == "overdue"


def test_override_requires_a_reason_and_an_authoriser(service):
    with pytest.raises(ValidationError, match="reason"):
        service.set_renewal_override("E001", "SHIRT", "2030-01-01",
                                     reason="  ", authorised_by="njiru")
    with pytest.raises(ValidationError, match="authorised"):
        service.set_renewal_override("E001", "SHIRT", "2030-01-01",
                                     reason="because", authorised_by="")


def test_clearing_an_override_restores_the_calculated_date(service):
    original = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    service.set_renewal_override("E001", "SHIRT", "2030-06-01",
                                 reason="trial", authorised_by="njiru")
    assert service.clear_renewal_override("E001", "SHIRT", who="njiru") is True
    restored = next(s for s in service.statuses_for("E001") if s.item_code == "SHIRT")
    assert restored.next_due == original.next_due


def test_clearing_an_override_that_is_not_there(service):
    assert service.clear_renewal_override("E001", "SHIRT", who="njiru") is False


def test_overrides_survive_a_reload(service):
    service.set_renewal_override("E001", "SHIRT", "2030-06-01",
                                 reason="extended wear trial", authorised_by="njiru")
    service.store.flush()
    service.store.load()
    saved = service.overrides_for("E001")["SHIRT"]
    assert saved.next_due == date(2030, 6, 1)
    assert saved.authorised_by == "njiru" and saved.reason == "extended wear trial"


def test_replacing_an_override_updates_the_same_row(service):
    service.set_renewal_override("E001", "SHIRT", "2030-06-01", reason="a", authorised_by="njiru")
    service.store.flush()
    service.set_renewal_override("E001", "SHIRT", "2031-06-01", reason="b", authorised_by="njiru")
    service.store.flush()
    rows = _sheet(service, "RenewalOverrides")
    assert len(rows) == 2, "one header plus one override, not a duplicate"


# --- the audit trail ---

def test_every_edit_is_logged_with_its_previous_value(service):
    service.edit_employee("E002", {"full_name": "Amina Y. Bello"},
                          who="njiru", reason="married name")
    service.store.flush()
    rows = _sheet(service, "ChangeLog")
    header = rows[0]
    entry = rows[-1]
    assert entry[header.index("Who")] == "njiru"
    assert entry[header.index("Record Type")] == "employee"
    assert entry[header.index("Field")] == "full_name"
    assert entry[header.index("Old Value")] == "Amina Yusuf"
    assert entry[header.index("New Value")] == "Amina Y. Bello"
    assert entry[header.index("Reason")] == "married name"


def test_overrides_are_logged_with_their_authorisation(service):
    service.set_renewal_override("E001", "SHIRT", "2030-06-01",
                                 reason="extended wear trial", authorised_by="njiru")
    service.store.flush()
    rows = _sheet(service, "ChangeLog")
    header = rows[0]
    entry = rows[-1]
    assert entry[header.index("Record Type")] == "renewal override"
    assert entry[header.index("Reason")] == "extended wear trial"
    assert entry[header.index("Who")] == "njiru"


def test_a_no_op_edit_writes_no_audit_row(service):
    """Nothing changed, so nothing is logged — and the sheet is never created."""
    before = service.employee("E002")
    service.edit_employee("E002", {"full_name": before.full_name}, who="njiru")
    service.store.flush()
    wb = load_workbook(service.store.path)
    has_log = "ChangeLog" in wb.sheetnames
    wb.close()
    assert not has_log


def test_an_edit_survives_a_row_being_inserted_in_excel(service):
    """Cached row numbers shift when someone inserts a row; keys do not."""
    wb = load_workbook(service.store.path)
    wb["Employees"].insert_rows(2)          # a human adds a row above everyone
    wb["Employees"].cell(row=2, column=1, value="E000")
    wb["Employees"].cell(row=2, column=2, value="Inserted Person")
    wb.save(service.store.path)
    wb.close()

    service.edit_employee("E002", {"full_name": "Amina Y. Bello"}, who="njiru")
    service.store.flush()

    rows = _sheet(service, "Employees")
    header = rows[0]
    by_number = {r[header.index("Employee Number")]: r for r in rows[1:] if r[0]}
    assert by_number["E002"][header.index("Full Name")] == "Amina Y. Bello"
    # the inserted row is untouched — the edit did not land on the wrong person
    assert by_number["E000"][header.index("Full Name")] == "Inserted Person"
