"""Santander de Titulizacion annual accounts (UCI 15/16). No fixture exists yet, so every document is routed to the
layout queue (review: LAYOUT_UNKNOWN) instead of being parsed by guesswork."""
from __future__ import annotations


def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[dict]:
    from . import review_row
    return [review_row(isin, url, "LAYOUT_UNKNOWN: santander_accounts has no fixture yet (add one, then parse)")]
