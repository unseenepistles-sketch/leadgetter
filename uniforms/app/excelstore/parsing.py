"""Turning whatever the client actually typed into domain objects.

Real spreadsheets are messy: columns get reordered and renamed, dates arrive in
four formats, and blank rows litter the bottom of every sheet. Everything here is
deliberately forgiving on input and strict about flagging what it could not
understand — a row we cannot parse becomes a visible problem, never a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

#: Excel's day zero. Serial 1 is 1900-01-01, and the format carries a well-known
#: phantom 29 Feb 1900, which is why serials above 59 are shifted by one day.
_EXCEL_EPOCH = date(1899, 12, 31)

_DATE_FORMATS_DAYFIRST = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d-%b-%y",
)
_DATE_FORMATS_MONTHFIRST = (
    "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y",
)

_TRUE = {"y", "yes", "true", "1", "active", "x", "t"}
_FALSE = {"n", "no", "false", "0", "inactive", "f", ""}


def normalise_header(value: Any) -> str:
    """Fold a header to a comparable key: ``"Employee No."`` -> ``employeeno``."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


@dataclass(frozen=True, slots=True)
class Column:
    """One field we want, and every header the client might have used for it."""

    field: str
    aliases: tuple[str, ...]
    required: bool = False

    def matches(self, header_key: str) -> bool:
        return header_key in {normalise_header(a) for a in self.aliases}


def map_headers(
    header_row: Sequence[Any], columns: Sequence[Column]
) -> tuple[dict[str, int], list[str]]:
    """Match sheet headers to fields by name, never by position.

    Returns the field -> column-index mapping plus a list of required fields that
    could not be found, so a bad sheet fails loudly at import rather than silently
    reading the wrong column.
    """
    keys = [normalise_header(h) for h in header_row]
    mapping: dict[str, int] = {}
    for col in columns:
        for idx, key in enumerate(keys):
            if key and col.matches(key) and col.field not in mapping:
                mapping[col.field] = idx
                break
    missing = [c.field for c in columns if c.required and c.field not in mapping]
    return mapping, missing


def cell(row: Sequence[Any], mapping: dict[str, int], field: str) -> Any:
    idx = mapping.get(field)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def parse_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else default


def parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def parse_date(value: Any, *, dayfirst: bool = True) -> Optional[date]:
    """Best-effort date parsing across the formats spreadsheets actually contain.

    ``dayfirst`` decides how ``03/04/2024`` is read — 3 April (UK, the default) or
    4 March (US). Genuinely ambiguous either way, so it is a configured decision
    rather than a guess, and it must match the client's locale.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _from_excel_serial(float(value))

    text = str(value).strip()
    if not text:
        return None
    # Excel sometimes hands back a datetime rendered as text.
    text = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$", "", text).strip()
    text = text.replace(",", " ")
    text = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)

    formats = _DATE_FORMATS_DAYFIRST if dayfirst else _DATE_FORMATS_MONTHFIRST
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if re.fullmatch(r"\d{4,6}", text):
        return _from_excel_serial(float(text))
    return None


def _from_excel_serial(serial: float) -> Optional[date]:
    if serial <= 0 or serial > 2_958_465:  # beyond 9999-12-31
        return None
    days = int(serial)
    # Excel models a 29 Feb 1900 that never existed; everything after 28 Feb 1900
    # is therefore one day ahead of reality.
    if days > 59:
        days -= 1
    try:
        return _EXCEL_EPOCH + timedelta(days=days)
    except OverflowError:
        return None


def is_blank_row(row: Iterable[Any]) -> bool:
    return all(v is None or str(v).strip() == "" for v in row)
