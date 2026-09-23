"""Resolve and VALIDATE document locations (PIPELINE_SPEC s.2): L1 reachable, L2 identity, L3 period, L4 content,
L5 integrity; L6 coverage is the gap list from validate.gaps. Also generates candidate URLs for known patterns."""
from __future__ import annotations

import calendar as _cal
import csv
import datetime as dt
import re
from pathlib import Path

from .sources import REGISTRY

BCP = "https://ind.millenniumbcp.pt/{lang}/Institucional/investidores/securitizacoes/Documents/{code}-InvestorReport/"
EDT = "https://edt-sg.com/intranet/fondos/{code}/"

L4_RX = {"bcp": r"Security Level Information", "santander": r"VALORES EMITIDOS|B\.T\.A'S SERIE|Cuentas Anuales",
         "edt": r"movimiento de los Bonos|Amortizaci.n\s+\d{2}\.\d{2}\.\d{4}|Serie A2",
         "tda": r"liquidaciones intermedias|FECHA DE PAGO"}
FUND_NAME_RX = {"bcp": r"Magellan Mortgages", "santander": r"U\.?C\.?I\.?", "edt": r"HIPOCAT", "tda": r"TDA CAM|MADRID RMBS"}


def read_manifest(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as h:
        return list(csv.DictReader(h))


def safe_name(url: str) -> str:
    return re.sub(r"[^\w.\-]", "_", url.split("//")[-1])[-120:]


def bcp_names(code: str, y: int, m: int):
    n = code[-1]
    yield f"{y}{m:02d}{code}_InvestorReport.pdf"
    yield f"Investor-Report-{y}-{m:02d}.pdf"
    yield f"Investor-Report-{y}{m:02d}.pdf"
    yield f"InvestorReport_{y}{m:02d}.pdf"
    yield f"InvestorReport{y}{m:02d}.pdf"
    yield f"InvestorReport_{y}-{m:02d}.pdf"
    yield f"{y}{m:02d}_{code}_InvestorReport.pdf"
    yield f"{code}_InvestorReport_{y}{m:02d}.pdf"
    yield f"Investor_Report_{y}_{m:02d}.pdf"
    for d in range(10, 21):
        yield f"Investor_Report_{d:02d}{m:02d}{y}.pdf"
        yield f"Investor_Report_{y}_{m:02d}_{d:02d}{m:02d}{y}.pdf"
        yield f"Magellan-Mortgages-No{n}-plc_{d:02d}{m:02d}{y}.pdf"


def pattern_urls(isin: str, months: list[tuple[int, int]]) -> list[list[str]]:
    """Groups of alternative URLs, one group per expected period (first hit wins)."""
    s = REGISTRY[isin]
    groups = []
    if s.family == "bcp":
        for y, m in months:
            groups.append([BCP.format(lang=lang, code=s.code) + nm for lang in ("pt", "en") for nm in bcp_names(s.code, y, m)])
    elif s.family == "edt":
        for y, m in months:
            eom = _cal.monthrange(y, m)[1]
            st = f"{y % 100:02d}{m:02d}{eom:02d}"
            base = EDT.format(code=s.code)
            groups.append([base + f"E{s.code}N01{st}.pdf", base + f"M{s.code}N01{st}.pdf", base + f"I{s.code}N01{st}.pdf"])
            if m == 12:
                groups.append([base + f"M{s.code}C01{st}.pdf", base + f"E{s.code}C01{st}.pdf"])
    return groups


def check_document(row: dict, text: str | None, sha: str | None) -> dict:
    isin = row["isin"]
    fam = REGISTRY[isin].family if isin in REGISTRY else ""
    out = {"isin": isin, "doc_type": row.get("doc_type", ""), "period": row.get("period", ""), "url": row["url"]}
    if text is None:
        out.update(L1="SOURCE_UNAVAILABLE", L2="", L3="", L4="", L5="", status="DEAD_OR_BLOCKED")
        return out
    out["L1"] = "PASS"
    ident = isin in text or bool(re.search(FUND_NAME_RX.get(fam, "^$"), text, re.I))
    out["L2"] = "PASS" if ident else "MISMATCH"
    per = row.get("period", "")
    y = per[:4] if re.match(r"\d{4}", per) else ""
    out["L3"] = "PASS" if (not y or y in text) else "CHECK_PERIOD"
    out["L4"] = "PASS" if re.search(L4_RX.get(fam, "."), text, re.I) else "LAYOUT_UNKNOWN"
    out["L5"] = sha or ""
    out["status"] = "VALID" if out["L2"] == "PASS" and out["L4"] == "PASS" else \
        ("MISMATCH" if out["L2"] != "PASS" else "LAYOUT_UNKNOWN")
    return out


def expected_months(isin: str, deal: dict, today: dt.date) -> list[tuple[int, int]]:
    rules = deal.get("ipd_rule")
    first = deal.get("first_ipd")
    if not rules or not first:
        return []
    rules = rules if isinstance(rules, list) else [rules]
    months = rules[-1]["months"]
    d0 = dt.date.fromisoformat(str(first))
    out = []
    y, m = d0.year, d0.month
    while dt.date(y, m, 1) <= today:
        if m in months:
            out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out
