"""The order lifecycle: placed -> partially delivered -> delivered -> renewing."""
from __future__ import annotations

from datetime import date

import pytest

from app.domain.models import OrderStatus, order_status
from app.excelstore.workbook import WorkbookStore
from app.service import NotFound, UniformService, ValidationError
from tests.fixtures import clean_workbook


@pytest.fixture
def service(tmp_path):
    store = WorkbookStore(clean_workbook(tmp_path / "w.xlsx"), backup_dir=tmp_path / "bak")
    store.load()
    return UniformService(store)


# --- the status rule itself ---

@pytest.mark.parametrize(
    "qty,delivered,expected",
    [
        (2, 0, OrderStatus.PENDING),
        (2, 1, OrderStatus.PARTIAL),
        (2, 2, OrderStatus.DELIVERED),
        (2, 3, OrderStatus.DELIVERED),   # over-delivery still counts as done
        (1, 0, OrderStatus.PENDING),
    ],
)
def test_order_status(qty, delivered, expected):
    assert order_status(qty, delivered) is expected


def test_cancelled_beats_everything():
    assert order_status(2, 1, cancelled=True) is OrderStatus.CANCELLED


# --- placing ---

def test_place_order_creates_one_row_per_item(service):
    created = service.place_order("E002", {"SHIRT": 6, "TROUSER": 3})
    assert len(created) == 2
    assert {o.quantity for o in created} == {6, 3}
    assert len({o.order_id for o in created}) == 2


def test_order_ids_are_readable_and_unique(service):
    created = service.place_order("E002", {"SHIRT": 1, "TROUSER": 1},
                                  ordered_date=date(2026, 8, 18))
    assert [o.order_id for o in created] == ["ORD-20260818-001", "ORD-20260818-002"]


def test_zero_quantities_are_ignored(service):
    created = service.place_order("E002", {"SHIRT": 2, "TROUSER": 0, "BLAZER": 0})
    assert [o.item_code for o in created] == ["SHIRT"]


def test_an_order_of_nothing_is_rejected(service):
    with pytest.raises(ValidationError):
        service.place_order("E002", {"SHIRT": 0})


def test_unknown_item_is_rejected(service):
    with pytest.raises(ValidationError):
        service.place_order("E002", {"GHOST": 1})


def test_unknown_employee_is_rejected(service):
    with pytest.raises(NotFound):
        service.place_order("NOPE", {"SHIRT": 1})


# --- delivering ---

def test_new_order_is_awaiting_delivery(service):
    order = service.place_order("E002", {"SHIRT": 2})[0]
    line = service.order_line(order.order_id)
    assert line.status is OrderStatus.PENDING
    assert line.delivered == 0 and line.pending == 2


def test_part_delivery_reports_partially_delivered(service):
    """Her 'Jacket · 1/2 delivered' case."""
    order = service.place_order("E002", {"SHIRT": 2})[0]
    service.receive_delivery(order.order_id, quantity=1)
    line = service.order_line(order.order_id)
    assert line.status is OrderStatus.PARTIAL
    assert (line.delivered, line.pending) == (1, 1)


def test_delivering_the_rest_closes_the_order(service):
    order = service.place_order("E002", {"SHIRT": 2})[0]
    service.receive_delivery(order.order_id, quantity=1)
    service.receive_delivery(order.order_id, quantity=1)
    line = service.order_line(order.order_id)
    assert line.status is OrderStatus.DELIVERED
    assert line.pending == 0


def test_delivery_defaults_to_everything_outstanding(service):
    order = service.place_order("E002", {"SHIRT": 3})[0]
    service.receive_delivery(order.order_id)
    assert service.order_line(order.order_id).status is OrderStatus.DELIVERED


def test_cannot_deliver_more_than_was_ordered(service):
    order = service.place_order("E002", {"SHIRT": 2})[0]
    with pytest.raises(ValidationError, match="outstanding"):
        service.receive_delivery(order.order_id, quantity=3)


def test_cannot_deliver_a_closed_order(service):
    order = service.place_order("E002", {"SHIRT": 1})[0]
    service.receive_delivery(order.order_id)
    with pytest.raises(ValidationError, match="already fully delivered"):
        service.receive_delivery(order.order_id)


def test_future_dated_delivery_is_rejected(service):
    order = service.place_order("E002", {"SHIRT": 1})[0]
    with pytest.raises(ValidationError):
        service.receive_delivery(order.order_id, received_date=date(2099, 1, 1))


def test_unknown_order_is_not_found(service):
    with pytest.raises(NotFound):
        service.receive_delivery("ORD-NOPE")


# --- delivery is the handover, and starts the renewal clock ---

def test_delivery_records_an_issuance_linked_to_the_order(service):
    order = service.place_order("E002", {"SHIRT": 1})[0]
    issued = service.receive_delivery(order.order_id, received_date=date(2026, 8, 18))
    assert issued.order_id == order.order_id
    assert issued.cycle_months == 12
    assert issued.size == "M"  # prefilled from the employee's profile


def test_delivery_moves_the_employee_out_of_never_issued(service):
    before = {s.item_code: s.status.value for s in service.statuses_for("E002")}
    assert before["SHIRT"] == "never_issued"

    order = service.place_order("E002", {"SHIRT": 1})[0]
    service.receive_delivery(order.order_id)

    after = {s.item_code: s.status.value for s in service.statuses_for("E002")}
    assert after["SHIRT"] == "ok"


def test_renewal_clock_runs_from_delivery_not_from_the_order(service):
    """Ordered in January, delivered in June — the cycle starts in June."""
    order = service.place_order("E002", {"SHIRT": 1}, ordered_date=date(2026, 1, 10))[0]
    service.receive_delivery(order.order_id, received_date=date(2026, 6, 10))
    line = service.order_line(order.order_id)
    assert line.next_due == date(2027, 6, 10)


# --- rollups the overview needs ---

def test_pending_delivery_counts_pieces_not_orders(service):
    service.place_order("E002", {"SHIRT": 6, "TROUSER": 3})
    assert service.pending_delivery() == 9


def test_pending_delivery_drops_as_stock_arrives(service):
    order = service.place_order("E002", {"SHIRT": 4})[0]
    service.receive_delivery(order.order_id, quantity=3)
    assert service.pending_delivery() == 1


def test_filtering_the_orders_log(service):
    a = service.place_order("E002", {"SHIRT": 2})[0]
    service.place_order("E003", {"TROUSER": 1})
    service.receive_delivery(a.order_id, quantity=1)

    assert len(service.order_lines()) == 2
    assert len(service.order_lines(status="partially_delivered")) == 1
    assert len(service.order_lines(status="pending")) == 1
    assert len(service.order_lines(employee_number="E003")) == 1
    assert len(service.order_lines(item_code="SHIRT")) == 1


def test_unknown_order_status_filter_is_rejected(service):
    with pytest.raises(ValidationError):
        service.order_lines(status="banana")


# --- action needed is prioritised the way the counter works ---

def test_action_needed_puts_overdue_above_undelivered(service):
    service.place_order("E002", {"SHIRT": 1})
    rows = service.action_needed(today=date(2026, 8, 18))
    kinds = [r["kind"] for r in rows]
    assert kinds, "expected something to act on"
    # Every renewal outranks every order line.
    assert kinds.index("renewal") < (kinds.index("order") if "order" in kinds else 99)


def test_action_needed_describes_part_deliveries(service):
    order = service.place_order("E002", {"SHIRT": 2})[0]
    service.receive_delivery(order.order_id, quantity=1)
    rows = service.action_needed(today=date(2026, 8, 18))
    orders = [r for r in rows if r["kind"] == "order"]
    assert orders and orders[0]["detail"] == "1/2 delivered"


# --- persistence ---

def test_orders_survive_a_reload(service, tmp_path):
    order = service.place_order("E002", {"SHIRT": 2}, supplier_ref="PO-88")[0]
    service.receive_delivery(order.order_id, quantity=1)
    service.store.flush()
    service.store.load()

    line = service.order_line(order.order_id)
    assert line.status is OrderStatus.PARTIAL
    assert line.delivered == 1
    assert line.order.supplier_ref == "PO-88"


def test_orders_and_issuances_flush_together_to_their_own_sheets(service):
    from openpyxl import load_workbook

    order = service.place_order("E002", {"SHIRT": 1})[0]
    service.receive_delivery(order.order_id)
    assert service.store.flush() == 2  # one order row, one issuance row

    wb = load_workbook(service.store.path)
    assert len(list(wb["Orders"].iter_rows(min_row=2, values_only=True))) == 1
    assert len(list(wb["Issuances"].iter_rows(min_row=2, values_only=True))) == 5
    wb.close()


def test_a_legacy_sheet_without_an_order_id_column_gains_one(service):
    """Client workbooks predate this feature — the link must not be dropped."""
    from openpyxl import load_workbook

    before = load_workbook(service.store.path)
    header = next(before["Issuances"].iter_rows(min_row=1, max_row=1, values_only=True))
    before.close()
    assert "Order ID" not in header, "fixture should start without the column"

    order = service.place_order("E002", {"SHIRT": 2})[0]
    service.receive_delivery(order.order_id, quantity=1)
    service.store.flush()

    wb = load_workbook(service.store.path)
    header = next(wb["Issuances"].iter_rows(min_row=1, max_row=1, values_only=True))
    last = list(wb["Issuances"].iter_rows(values_only=True))[-1]
    wb.close()
    assert "Order ID" in header
    assert last[header.index("Order ID")] == order.order_id

    service.store.load()
    assert service.order_line(order.order_id).delivered == 1
