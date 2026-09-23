"""EdT annual accounts, Nota 'movimiento de los Bonos' (kEUR). Layouts CA2016 and CA2019 (Hipocat 11)."""
from __future__ import annotations

import re

import rmbs_harvester as H

from ._common import normalise


def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[dict]:
    from . import review_row
    ser = series or H.SERIES_BY_ISIN.get(isin)
    if not ser:
        return [review_row(isin, url, "SERIES_UNMAPPED")]
    rows = H.parse_edt_accounts(text, isin, url, series=ser)
    if not rows:
        return [review_row(isin, url, f"LAYOUT_UNKNOWN: no 'Amortizacion dd.mm.yyyy' rows for Serie {ser}")]
    out = [normalise(r) for r in rows]
    out += parse_due_paid(text, isin, url, ser)
    return merge_due(out)


DUE_RX = re.compile(r"(\d{2}[./]\d{2}[./]\d{4})\s+Serie\s+(\w+)\s+(\d{1,3}(?:\.\d{3})*)\s+(\d{1,3}(?:\.\d{3})*|-)\s+"
                    r"(\d{1,3}(?:\.\d{3})*|-)")


def parse_due_paid(text: str, isin: str, url: str, ser: str) -> list[dict]:
    """Per-date principal 'Devengado periodo / Liquidado / Insuficiencia fondos disponibles' lines (kEUR).
    Returns partial rows carrying principal_due / principal_shortfall only (merged by date)."""
    out = []
    if not re.search(r"Insuficiencia", text, re.I):
        return out
    for d, s, due, paid, short in DUE_RX.findall(text):
        if s != ser:
            continue
        k = lambda x: 0.0 if x == "-" else float(x.replace(".", "")) * 1e3
        dd, mm, yy = re.split(r"[./]", d)
        out.append({"payment_date": f"{yy}-{mm}-{dd}", "principal_due": k(due), "principal_shortfall": k(short),
                    "_paid": k(paid), "_due_only": True})
    return out


def merge_due(rows: list[dict]) -> list[dict]:
    base = [r for r in rows if not r.get("_due_only")]
    for d in (r for r in rows if r.get("_due_only")):
        for r in base:
            if r["payment_date"] == d["payment_date"]:
                r["principal_due"], r["principal_shortfall"] = d["principal_due"], d["principal_shortfall"]
    return base
