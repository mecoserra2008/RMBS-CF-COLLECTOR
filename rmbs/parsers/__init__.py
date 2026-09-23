"""Parser contract: ``parse(text, isin, url, *, series=None) -> list[dict]`` -- pure, no I/O, never raises on an
unknown layout (returns a ``review: <reason>`` row instead). Numbers come from named sections only.

``detect(text)`` names the layout; ``parse_any`` dispatches. Layout catalogue: docs/LAYOUTS.md.
"""
from __future__ import annotations

import re

from ..schema import SCHEMA
from . import bcp, edt_accounts, santander_accounts, tda_accounts, tda_notice, uci

PARSERS = {"bcp_investor_report": bcp.parse, "uci_informacion_periodica": uci.parse,
           "edt_accounts": edt_accounts.parse, "tda_accounts": tda_accounts.parse,
           "tda_notice": tda_notice.parse, "santander_accounts": santander_accounts.parse}


def review_row(isin: str, url: str, reason: str, section: str = "") -> dict:
    r = {c: "" for c in SCHEMA}
    r.update(isin=isin, source_url=url, source_section=section or "n/a", parse_status=f"review: {reason}")
    return r


def detect(text: str) -> str:
    t = text[:200000]
    if "Security Level Information" in t and "Magellan Mortgages" in t:
        return "bcp_investor_report"
    if "VALORES EMITIDOS" in t.upper() or "B.T.A'S SERIE" in t:
        return "uci_informacion_periodica"
    if re.search(r"FECHA DE PAGO:\s*\d", t) and "INFORMACION A LOS INVERSORES" in t.upper():
        return "tda_notice"
    if re.search(r"Liquidaci.n de pagos de las liquidaciones intermedias", t, re.I):
        return "tda_accounts"
    if re.search(r"Amortizaci.n\s*\n?\s*\d{2}\.\d{2}\.\d{4}", t) and "Serie" in t:
        return "edt_accounts"
    if re.search(r"U\.?C\.?I\.? ?1[56]", t) and re.search(r"cuentas anuales", t, re.I):
        return "santander_accounts"
    return "unknown"


def parse_any(text: str, isin: str, url: str, *, series: str | None = None) -> tuple[str, list[dict]]:
    layout = detect(text)
    if layout == "unknown":
        return layout, [review_row(isin, url, "LAYOUT_UNKNOWN")]
    try:
        return layout, PARSERS[layout](text, isin, url, series=series)
    except Exception as e:     # contract: never raise on a layout surprise
        return layout, [review_row(isin, url, f"{layout} parser error {type(e).__name__}: {str(e)[:60]}")]
