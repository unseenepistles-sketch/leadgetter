"""Tiny persisted key-value for the operator's own settings.

Stores the current offer, sender name, and last-used niche so discovery,
outreach and campaign drafting share sensible defaults across page loads.
Backed by a small JSON file — no database migration needed for preferences.
"""
from __future__ import annotations

import json
from pathlib import Path

_PATH = Path("data/state.json")
_DEFAULTS = {
    "offer": "",
    "sender_name": "",
    "niche": "",
    "location": "",
    "platform": "instagram",
}


def load() -> dict:
    data = dict(_DEFAULTS)
    try:
        data.update(json.loads(_PATH.read_text()))
    except Exception:  # noqa: BLE001 - missing/corrupt file -> defaults
        pass
    return data


def save(**kwargs) -> dict:
    data = load()
    for key, value in kwargs.items():
        if key in _DEFAULTS and value is not None:
            data[key] = value
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2))
    return data
