from __future__ import annotations

from ..schema import SCHEMA


def normalise(row: dict) -> dict:
    """Project a harvester row onto SCHEMA (missing keys -> '')."""
    return {c: row.get(c, "") for c in SCHEMA}
