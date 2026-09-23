"""Per-ISIN payment history store (out/<ISIN>_<ticker>.csv, schema = SCHEMA)."""
from __future__ import annotations

import csv
from pathlib import Path

from .schema import NUMERIC, SCHEMA, fmt
from .sources import REGISTRY

RANK = {"ok": 0, "DERIVED": 1, "PARTIAL": 2, "review": 3, "FAIL": 4, "ERROR": 5}


def status_rank(st: str) -> int:
    for k, v in RANK.items():
        if str(st).startswith(k):
            return v
    return 9


def coerce(r: dict) -> dict:
    out = {}
    for c in SCHEMA:
        v = r.get(c, "")
        v = "" if v is None else v
        if c in NUMERIC and v != "":
            try:
                f = float(v)
                v = int(f) if c in ("n_notes", "accrual_days", "margin_bp") and f.is_integer() else f
            except (TypeError, ValueError):
                pass
        out[c] = v
    return out


def path_for(out_dir: Path, isin: str) -> Path:
    return Path(out_dir) / REGISTRY[isin].csv_name


def load(out_dir: Path, isin: str) -> list[dict]:
    p = path_for(out_dir, isin)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as h:
        rows = [coerce(r) for r in csv.DictReader(h)]
    return sorted(rows, key=lambda r: r["payment_date"])


def merge(existing: list[dict], new: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep one row per payment_date: the better status wins; on a tie the existing row stays (deterministic).
    Returns (rows, log)."""
    by = {r["payment_date"]: r for r in existing if r.get("payment_date")}
    log = []
    for r in sorted((coerce(x) for x in new if x.get("payment_date")), key=lambda r: (r["payment_date"], r["source_url"])):
        d = r["payment_date"]
        if d not in by:
            by[d] = r
            log.append(f"added {d} ({r['parse_status'][:20]})")
        elif status_rank(r["parse_status"]) < status_rank(by[d]["parse_status"]):
            log.append(f"upgraded {d}: {by[d]['parse_status'][:20]} -> {r['parse_status'][:20]}")
            by[d] = r
        else:   # same or worse: fill blank fields only, never overwrite a filed number
            for c in SCHEMA:
                if by[d][c] == "" and r[c] != "" and not c.startswith(("source", "parse")):
                    by[d][c] = r[c]
    return [by[k] for k in sorted(by)], log


def write(out_dir: Path, isin: str, rows: list[dict]) -> Path:
    p = path_for(out_dir, isin)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h, lineterminator="\n")
        w.writerow(SCHEMA)
        for r in sorted(rows, key=lambda r: r["payment_date"]):
            w.writerow([fmt(r.get(c, "")) for c in SCHEMA])
    return p


def fnum(r: dict, k: str):
    v = r.get(k, "")
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_kEUR(r: dict) -> bool:
    return "kEUR" in str(r.get("source_section", "")) or "annual accounts" in str(r.get("parse_status", ""))
