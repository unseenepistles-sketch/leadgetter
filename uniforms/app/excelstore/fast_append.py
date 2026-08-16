"""Appending rows to a large .xlsx without re-parsing the whole workbook.

Loading a 70,000-row workbook through openpyxl and saving it back costs ~15
seconds. Recording a uniform handover must not cost that, and at the client's
volume a queue of such writes would never drain.

An .xlsx is a zip of XML parts. Appending rows only needs the one worksheet part
edited — splice ``<row>`` elements in before ``</sheetData>`` and copy every other
entry through byte-for-byte. That turns a 15-second rewrite into well under a
second, and it touches nothing else in the file, so the client's formatting,
formulas, filters and extra sheets survive untouched.

Two details that matter for the result looking native in Excel:

* **Styles are copied from the last existing row**, per column, so appended dates
  render in the same format as the ones already there rather than as raw serials.
* **Strings are written inline** (``t="inlineStr"``) so ``sharedStrings.xml`` never
  has to be rewritten — which is what would drag the whole-file cost back in.

If anything about the file is not understood, callers fall back to the openpyxl
path. This is an optimisation, never the only way to write.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from xml.sax.saxutils import escape

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

#: Excel's epoch, with the phantom 29 Feb 1900 already accounted for.
_SERIAL_EPOCH = date(1899, 12, 30)


class FastAppendUnsupported(RuntimeError):
    """This workbook is not one we can splice safely — use the slow path."""


def column_letter(index: int) -> str:
    """1-based column index to its spreadsheet letter."""
    if index < 1:
        raise ValueError("column index is 1-based")
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def to_serial(value: date | datetime) -> float:
    if isinstance(value, datetime):
        days = (value.date() - _SERIAL_EPOCH).days
        seconds = value.hour * 3600 + value.minute * 60 + value.second
        return days + seconds / 86400
    return float((value - _SERIAL_EPOCH).days)


def append_rows(path: str | Path, sheet_name: str, rows: Sequence[Sequence[Any]]) -> int:
    """Append rows to ``sheet_name``, returning how many were written.

    Writes to a temporary file and atomically replaces the original, so a crash
    part-way through cannot leave a corrupt workbook.
    """
    target = Path(path)
    if not rows:
        return 0

    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        sheet_part = _locate_sheet(zf, sheet_name)
        if sheet_part not in names:
            raise FastAppendUnsupported(f"worksheet part {sheet_part} missing")
        xml = zf.read(sheet_part).decode("utf-8")

    updated = _splice(xml, rows)

    tmp = target.with_name(f".{target.stem}.fastappend.xlsx")
    try:
        with zipfile.ZipFile(target) as src, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED
        ) as dst:
            for item in src.infolist():
                if item.filename == sheet_part:
                    dst.writestr(item, updated)
                else:
                    # Byte-copy: nothing else in the workbook is touched.
                    dst.writestr(item, src.read(item.filename))
        shutil.copystat(target, tmp)
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return len(rows)


def _locate_sheet(zf: zipfile.ZipFile, sheet_name: str) -> str:
    """Resolve a sheet's display name to its worksheet part inside the zip."""
    import xml.etree.ElementTree as ET

    try:
        book = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError) as exc:
        raise FastAppendUnsupported(f"cannot read workbook structure: {exc}") from exc

    rid: Optional[str] = None
    for node in book.iter(f"{_NS}sheet"):
        if node.get("name") == sheet_name:
            rid = node.get(_RID)
            break
    if rid is None:
        raise FastAppendUnsupported(f"sheet {sheet_name!r} not found")

    for rel in rels.iter(f"{_REL_NS}Relationship"):
        if rel.get("Id") == rid:
            tgt = (rel.get("Target") or "").lstrip("/")
            return tgt if tgt.startswith("xl/") else f"xl/{tgt}"
    raise FastAppendUnsupported(f"no relationship for {rid}")


def _splice(xml: str, rows: Sequence[Sequence[Any]]) -> str:
    last_row, styles = _inspect(xml)
    built = "".join(
        _row_xml(last_row + offset + 1, values, styles)
        for offset, values in enumerate(rows)
    )

    if "</sheetData>" in xml:
        xml = xml.replace("</sheetData>", f"{built}</sheetData>", 1)
    else:
        # An empty sheet serialises as <sheetData/>.
        match = re.search(r"<sheetData\s*/>", xml)
        if not match:
            raise FastAppendUnsupported("no <sheetData> element")
        xml = xml[: match.start()] + f"<sheetData>{built}</sheetData>" + xml[match.end():]

    # A stale <dimension> makes some readers truncate the sheet.
    width = max((len(r) for r in rows), default=1)
    return re.sub(
        r'<dimension ref="[^"]*"\s*/>',
        f'<dimension ref="A1:{column_letter(max(width, 1))}{last_row + len(rows)}"/>',
        xml,
        count=1,
    )


def _inspect(xml: str) -> tuple[int, dict[str, str]]:
    """Highest row number present, and each column's style from the last row."""
    rows = list(re.finditer(r'<row[^>]*\sr="(\d+)"[^>]*>(.*?)</row>', xml, re.S))
    if not rows:
        return 0, {}
    last = max(rows, key=lambda m: int(m.group(1)))
    styles: dict[str, str] = {}
    for cellmatch in re.finditer(r'<c\s+([^>]*?)/?>', last.group(2)):
        attrs = cellmatch.group(1)
        ref = re.search(r'r="([A-Z]+)\d+"', attrs)
        style = re.search(r's="(\d+)"', attrs)
        if ref and style:
            styles[ref.group(1)] = style.group(1)
    return int(last.group(1)), styles


def _row_xml(row_number: int, values: Sequence[Any], styles: dict[str, str]) -> str:
    cells = []
    for idx, value in enumerate(values, start=1):
        if value is None or value == "":
            continue
        letter = column_letter(idx)
        cells.append(_cell_xml(f"{letter}{row_number}", value, styles.get(letter)))
    return f'<row r="{row_number}">{"".join(cells)}</row>'


def _cell_xml(ref: str, value: Any, style: Optional[str]) -> str:
    style_attr = f' s="{style}"' if style else ""
    if isinstance(value, bool):
        return f'<c r="{ref}"{style_attr} t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (date, datetime)):
        # Reusing the column's existing style keeps the date rendering identical
        # to the rows already in the sheet.
        return f'<c r="{ref}"{style_attr}><v>{to_serial(value):g}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
