"""Schemas for every CSV the pipeline writes, plus a light-weight enforcement layer (pandera-equivalent).

History rows use ``rmbs_harvester.SCHEMA`` (the History sheet column order) -- one definition, imported here.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import rmbs_harvester as _H

SCHEMA: list[str] = list(_H.SCHEMA)

NUMERIC = {"accrual_days", "index_rate", "margin_bp", "coupon_rate", "original_balance", "beg_balance",
           "principal_paid", "end_balance", "pool_factor", "interest_paid", "pdl_balance", "n_notes",
           "principal_per_note", "denom_beg", "denom_end", "coll_beg_balance", "coll_sched_principal",
           "coll_prepayments", "coll_repurchases", "coll_cpr_reported", "coll_deemed_losses", "coll_recoveries",
           "coll_end_balance", "coll_end_balance_net", "wac", "arrears_90_365", "cum_default_ratio",
           "reserve_balance", "coll_principal_total", "coll_cpr_12m", "next_coupon_rate", "next_index_rate",
           "principal_due", "principal_shortfall", "sub_beg_balance", "sub_end_balance", "coll_beg_balance_net"}
DATES = {"payment_date", "accrual_start", "accrual_end"}
REQUIRED = {"isin", "payment_date", "source_url", "source_section", "parse_status"}
STATUS_PREFIXES = ("ok", "DERIVED", "PARTIAL", "review", "FAIL", "ERROR")

# Output tables (name -> columns). Every writer uses these; selfcheck re-validates them.
TABLES: dict[str, list[str]] = {
    "validation": ["isin", "payment_date", "check", "result", "detail"],
    "gaps": ["isin", "ticker", "expected_ipd", "prev_observed", "next_observed", "status"],
    "yearend_reconciliation": ["isin", "date", "checkpoint_kEUR", "chain_kEUR", "diff_kEUR", "tolerance_kEUR",
                               "result", "source_url"],
    "reproduction_gate": ["isin", "ticker", "n_replayed", "G1", "G2", "G3", "G4", "G5", "G6", "gate_status",
                          "detail"],
    "champion_models": ["isin", "cpr_model", "cdr_model", "recovery", "call", "params_json", "S", "U_h1",
                        "E_dm_h4", "E_fac_h1", "coverage", "n_origins", "gates_passed", "fitted_at", "status"],
    "model_scores": ["isin", "candidate", "cpr_model", "cdr_model", "recovery", "call", "n_params", "h",
                     "n_obs", "E_fac", "E_prin", "E_cum", "E_wal", "E_dm", "U", "CRPS", "COV", "BIAS", "STAB",
                     "S", "win_share"],
    "uncertainty": ["isin", "payment_date", "step", "metric", "p5", "p25", "p50", "p75", "p95"],
    "bbg_vs_model": ["isin", "metric", "bbg", "model", "realised", "winner", "status"],
    "pricing": ["isin", "ticker", "status", "valuation_date", "balance", "price", "dm_bp", "wal_to_call",
                "wal_to_maturity", "spread_duration", "call_assumption", "model"],
    "location_checks": ["isin", "doc_type", "period", "url", "L1", "L2", "L3", "L4", "L5", "status"],
    "calendar_checks": ["isin", "observed", "projected", "result"],
    "stage_status": ["stage", "status", "detail"],
}


class SchemaError(ValueError):
    pass


def _isnum(x) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v)


def check_history_rows(rows: list[dict]) -> list[str]:
    """Return the list of schema violations (empty = valid)."""
    errs: list[str] = []
    for i, r in enumerate(rows):
        if list(r.keys()) != SCHEMA and set(r.keys()) != set(SCHEMA):
            errs.append(f"row {i}: columns differ from SCHEMA")
            continue
        for k in REQUIRED:
            if r.get(k) in ("", None):
                errs.append(f"row {i} ({r.get('payment_date')}): missing {k}")
        for k in NUMERIC:
            v = r.get(k)
            if v not in ("", None) and not _isnum(v):
                errs.append(f"row {i}: {k}={v!r} not numeric")
        st = str(r.get("parse_status", ""))
        if st and not st.startswith(STATUS_PREFIXES):
            errs.append(f"row {i}: parse_status {st[:30]!r} not a known status")
    dates = [r["payment_date"] for r in rows]
    if dates != sorted(dates):
        errs.append("rows not sorted by payment_date")
    if len(set(dates)) != len(dates):
        errs.append("duplicate payment_date")
    return errs


def check_table(path: Path, name: str) -> list[str]:
    cols = TABLES[name]
    with open(path, newline="", encoding="utf-8") as h:
        rd = csv.reader(h)
        hdr = next(rd, None)
        if hdr != cols:
            return [f"{path.name}: header {hdr} != {cols}"]
        return [f"{path.name}: line {i + 2} has {len(r)} fields" for i, r in enumerate(rd) if len(r) != len(cols)]


def fmt(v) -> str:
    """Canonical, deterministic number formatting for every CSV cell."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return ""
        v = round(v, 8) + 0.0          # +0.0 folds -0.0 into 0.0
        return repr(v)
    return str(v)


def write_table(path: Path, name: str, rows: list[dict]) -> None:
    cols = TABLES[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as h:
        w = csv.writer(h, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(r.get(c, "")) for c in cols])
    errs = check_table(path, name)
    if errs:
        raise SchemaError("; ".join(errs))
