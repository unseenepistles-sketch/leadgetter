"""The XML splice path must produce files Excel and openpyxl both accept."""
from __future__ import annotations

import zipfile
from datetime import date, datetime

import pytest
from openpyxl import Workbook, load_workbook

from app.excelstore.fast_append import (
    FastAppendUnsupported,
    append_rows,
    column_letter,
    to_serial,
)
from tests.fixtures import clean_workbook


@pytest.mark.parametrize(
    "index,letter", [(1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA"), (702, "ZZ"), (703, "AAA")]
)
def test_column_letter(index, letter):
    assert column_letter(index) == letter


def test_column_letter_rejects_zero():
    with pytest.raises(ValueError):
        column_letter(0)


@pytest.mark.parametrize(
    "value,serial",
    [
        (date(1900, 1, 1), 2.0),
        (date(2024, 1, 15), 45306.0),
        (date(2021, 9, 1), 44440.0),
    ],
)
def test_to_serial_matches_excels_numbering(value, serial):
    assert to_serial(value) == serial


def test_serial_round_trips_through_openpyxl(tmp_path):
    """The proof the epoch is right: write a serial, read back the same date."""
    path = clean_workbook(tmp_path / "w.xlsx")
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 3, 7), 1, "M", "s", 12, ""]])
    wb = load_workbook(path)
    value = list(wb["Issuances"].iter_rows(values_only=True))[-1][2]
    wb.close()
    assert value == datetime(2024, 3, 7)


def test_appends_in_order_and_keeps_values(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    append_rows(path, "Issuances", [
        ["E002", "SHIRT", date(2024, 6, 1), 3, "M", "stores", 12, "first"],
        ["E003", "BLAZER", date(2024, 6, 2), 1, "44R", "stores", 24, "second"],
    ])
    wb = load_workbook(path)
    rows = list(wb["Issuances"].iter_rows(min_row=2, values_only=True))
    wb.close()
    assert len(rows) == 6
    assert rows[-2][0] == "E002" and rows[-2][7] == "first"
    assert rows[-1][0] == "E003" and rows[-1][3] == 1


def test_row_numbers_stay_contiguous(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 6, 1)]])
    append_rows(path, "Issuances", [["E003", "SHIRT", date(2024, 6, 2)]])
    wb = load_workbook(path)
    rows = list(wb["Issuances"].iter_rows(values_only=True))
    wb.close()
    assert len(rows) == 7  # header + 4 original + 2 appended
    assert all(r[0] is not None for r in rows[1:])  # no blank gap rows


def test_leaves_other_sheets_untouched(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 6, 1)]])
    wb = load_workbook(path)
    assert wb.sheetnames == ["Employees", "UniformItems", "Entitlements", "Issuances"]
    assert len(list(wb["Employees"].iter_rows(min_row=2, values_only=True))) == 4
    wb.close()


def test_output_is_a_valid_zip_with_all_original_parts(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    with zipfile.ZipFile(path) as zf:
        before = set(zf.namelist())
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 6, 1)]])
    with zipfile.ZipFile(path) as zf:
        assert zf.testzip() is None
        assert set(zf.namelist()) == before


def test_strings_are_escaped(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    nasty = 'Bell & Sons <"quoted">'
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 6, 1), 1, "M", "s", 12, nasty]])
    wb = load_workbook(path)
    assert list(wb["Issuances"].iter_rows(values_only=True))[-1][7] == nasty
    wb.close()


def test_none_cells_are_skipped_without_shifting_columns(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 6, 1), None, None, None, None, "tail"]])
    wb = load_workbook(path)
    row = list(wb["Issuances"].iter_rows(values_only=True))[-1]
    wb.close()
    assert row[0] == "E002"
    assert row[3] is None
    assert row[7] == "tail"  # still in the last column


def test_appending_to_an_empty_sheet(tmp_path):
    wb = Workbook()
    wb.remove(wb.active)
    wb.create_sheet("Issuances")
    wb.save(tmp_path / "empty.xlsx")
    wb.close()
    append_rows(tmp_path / "empty.xlsx", "Issuances", [["E001", "SHIRT", date(2024, 6, 1)]])
    wb = load_workbook(tmp_path / "empty.xlsx")
    rows = list(wb["Issuances"].iter_rows(values_only=True))
    wb.close()
    assert rows[0][0] == "E001"


def test_unknown_sheet_is_rejected_rather_than_guessed(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    with pytest.raises(FastAppendUnsupported):
        append_rows(path, "NoSuchSheet", [["x"]])


def test_empty_batch_is_a_no_op(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    assert append_rows(path, "Issuances", []) == 0


def test_no_temp_file_survives(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    append_rows(path, "Issuances", [["E002", "SHIRT", date(2024, 6, 1)]])
    assert not list(tmp_path.glob(".*.fastappend.xlsx"))


def test_failure_leaves_the_original_intact(tmp_path):
    path = clean_workbook(tmp_path / "w.xlsx")
    before = path.read_bytes()
    with pytest.raises(FastAppendUnsupported):
        append_rows(path, "Missing", [["x"]])
    assert path.read_bytes() == before
