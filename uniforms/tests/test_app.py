"""End-to-end tests through the real ASGI app, workbook and all."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from tests.fixtures import clean_workbook, messy_workbook


@pytest.fixture
def client(tmp_path, monkeypatch):
    workbook = clean_workbook(tmp_path / "uniforms.xlsx")
    monkeypatch.setenv("WORKBOOK_PATH", str(workbook))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bak"))
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("STORES_EMAIL", "stores@co.test")
    monkeypatch.setenv("REMINDERS_ENABLED", "0")

    from app import config, deps

    for cached in (config.get_settings, deps.get_store, deps.get_service, deps.get_ledger):
        cached.cache_clear()

    from app.main import app

    with TestClient(app) as c:
        yield c

    for cached in (config.get_settings, deps.get_store, deps.get_service, deps.get_ledger):
        cached.cache_clear()


# --- pages render ---

@pytest.mark.parametrize(
    "path",
    ["/", "/employees", "/employees/E001", "/issue", "/reports?state=never_issued", "/data-quality"],
)
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "<table" in response.text or "tile" in response.text


def test_unknown_employee_page_redirects_with_a_message(client):
    response = client.get("/employees/NOPE", follow_redirects=False)
    assert response.status_code == 303
    assert "err=" in response.headers["location"]


# --- the API ---

def test_employee_list_paginates(client):
    body = client.get("/api/employees?per_page=2").json()
    assert body["per_page"] == 2 and body["total"] == 4 and body["pages"] == 2
    assert len(body["items"]) == 2


def test_employee_search(client):
    body = client.get("/api/employees?q=amina").json()
    assert [e["employee_number"] for e in body["items"]] == ["E002"]


def test_active_only_filter_excludes_leavers(client):
    numbers = {e["employee_number"] for e in client.get("/api/employees?active_only=true").json()["items"]}
    assert "E004" not in numbers


def test_never_issued_report_finds_people_with_no_issuance_rows(client):
    """The headline requirement: invisible to any query over the issuance log."""
    body = client.get("/api/status?state=never_issued").json()
    assert body["total"] > 0
    assert any(i["employee_number"] == "E002" for i in body["items"])


def test_status_report_rejects_an_unknown_state(client):
    assert client.get("/api/status?state=banana").status_code == 400


def test_summary_counts_every_status(client):
    counts = client.get("/api/dashboard/summary").json()["counts"]
    assert counts["employees"] == 3  # E004 has left
    assert counts["outstanding"] >= 1


# --- writing through the API ---

def test_record_issuance_prefills_size_and_quantity(client):
    body = client.post(
        "/api/issuances", json={"employee_number": "E002", "item_code": "SHIRT"}
    ).json()
    assert body["size"] == "M"          # from the employee's profile
    assert body["quantity"] == 3        # from the role's entitlement
    assert body["cycle_months"] == 12   # snapshotted at issue time


def test_issuance_moves_the_employee_out_of_never_issued(client):
    before = client.get("/api/employees/E002/uniforms").json()["items"]
    assert {i["status"] for i in before} == {"never_issued"}

    client.post("/api/issuances", json={"employee_number": "E002", "item_code": "SHIRT"})

    after = {i["item_code"]: i["status"] for i in client.get("/api/employees/E002/uniforms").json()["items"]}
    assert after["SHIRT"] == "ok"
    assert after["BLAZER"] == "never_issued"


def test_issuance_reaches_the_workbook_on_disk(client, tmp_path):
    client.post("/api/issuances", json={"employee_number": "E002", "item_code": "SHIRT"})
    assert client.post("/api/workbook/flush").json()["written"] == 1

    wb = load_workbook(tmp_path / "uniforms.xlsx")
    last = list(wb["Issuances"].iter_rows(min_row=2, values_only=True))[-1]
    wb.close()
    assert last[0] == "E002" and last[1] == "SHIRT"


def test_future_dated_issuance_is_rejected(client):
    response = client.post(
        "/api/issuances",
        json={"employee_number": "E002", "item_code": "SHIRT", "issued_date": "2099-01-01"},
    )
    assert response.status_code == 400


def test_unknown_item_is_rejected(client):
    response = client.post(
        "/api/issuances", json={"employee_number": "E002", "item_code": "GHOST"}
    )
    assert response.status_code == 400


def test_unknown_employee_is_a_404(client):
    response = client.post("/api/issuances", json={"employee_number": "NOPE", "item_code": "SHIRT"})
    assert response.status_code == 404


def test_malformed_date_is_rejected(client):
    response = client.post(
        "/api/issuances",
        json={"employee_number": "E002", "item_code": "SHIRT", "issued_date": "15/01/2024"},
    )
    assert response.status_code == 400


def test_bulk_issue_covers_everyone_selected(client):
    body = client.post(
        "/api/issuances/bulk",
        json={"employee_numbers": ["E002", "E003"], "item_codes": ["SHIRT", "TROUSER"]},
    ).json()
    assert body["created"] == 4
    assert {i["employee_number"] for i in body["issuances"]} == {"E002", "E003"}


def test_bulk_issue_needs_both_sides(client):
    assert client.post("/api/issuances/bulk", json={"employee_numbers": [], "item_codes": ["SHIRT"]}).status_code == 400
    assert client.post("/api/issuances/bulk", json={"employee_numbers": ["E002"], "item_codes": []}).status_code == 400


# --- the issue form ---

def test_issue_form_records_and_redirects(client):
    response = client.post(
        "/issue",
        data={"employee_number": "E002", "item_codes": ["SHIRT", "TROUSER"], "issued_by": "stores"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "ok=" in response.headers["location"]
    statuses = {i["item_code"]: i["status"] for i in client.get("/api/employees/E002/uniforms").json()["items"]}
    assert statuses["SHIRT"] == "ok" and statuses["TROUSER"] == "ok"


def test_issue_form_with_no_items_reports_the_error(client):
    response = client.post(
        "/issue", data={"employee_number": "E002"}, follow_redirects=False
    )
    assert "err=" in response.headers["location"]


# --- reminders through the API ---

def test_reminder_dry_run_changes_nothing(client):
    body = client.post("/api/reminders/run?dry_run=true").json()
    assert body["dry_run"] and body["scanned"] > 0
    assert client.get("/api/reminders").json()["counts"] == {}


def test_reminder_live_run_then_repeat_is_silent(client):
    first = client.post("/api/reminders/run?dry_run=false").json()
    assert first["sent"] > 0
    second = client.post("/api/reminders/run?dry_run=false").json()
    assert second["sent"] == 0


# --- data quality surfaces a messy workbook ---

def test_data_quality_reports_problem_rows(tmp_path, monkeypatch):
    workbook = messy_workbook(tmp_path / "messy.xlsx")
    monkeypatch.setenv("WORKBOOK_PATH", str(workbook))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "bak"))
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "l.sqlite3"))

    from app import config, deps

    for cached in (config.get_settings, deps.get_store, deps.get_service, deps.get_ledger):
        cached.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        body = client.get("/api/data-quality").json()
        assert body["total"] > 0
        assert client.get("/data-quality").status_code == 200

    for cached in (config.get_settings, deps.get_store, deps.get_service, deps.get_ledger):
        cached.cache_clear()
