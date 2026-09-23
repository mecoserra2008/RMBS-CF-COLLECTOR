"""Read bbg/ exports if present. Missing files -> MANUAL_REQUIRED (never blocks the run)."""
from __future__ import annotations

import csv
from pathlib import Path

CF_COLS = ["isin", "run_id", "payment_date", "interest", "scheduled_principal", "prepayment", "default", "recovery",
           "loss", "ending_balance"]
RUN_COLS = ["run_id", "isin", "retrieved_at", "cft_mode", "tape_date", "settlement_date", "cpr", "cdr", "severity",
            "recovery_lag", "curve_id", "curve_date", "price", "dm", "wal", "call_assumption", "model_version"]
REPLINE_COLS = ["repline_id", "balance", "wac", "wam_months", "seasoning_months", "rate_type", "margin", "index",
                "io_flag", "ltv", "arrears_bucket", "region"]


def _read(p: Path, cols: list[str]) -> tuple[list[dict], str]:
    if not p.exists():
        return [], "MANUAL_REQUIRED: missing " + p.name
    with open(p, newline="", encoding="utf-8-sig") as h:
        rd = csv.DictReader(h)
        miss = [c for c in cols if c not in (rd.fieldnames or [])]
        if miss:
            return [], f"SCHEMA_ERROR: {p.name} lacks {','.join(miss)}"
        return list(rd), "ok"


def load(bbg_dir: Path) -> dict:
    bbg_dir = Path(bbg_dir)
    cf, s1 = _read(bbg_dir / "cashflows.csv", CF_COLS)
    runs, s2 = _read(bbg_dir / "runs.csv", RUN_COLS)
    status = "ok" if s1 == s2 == "ok" else (s1 if s1 != "ok" else s2)
    if cf and not runs:
        status = "UNUSABLE: cashflows.csv without runs.csv (configuration not captured)"
        cf = []
    return {"cashflows": cf, "runs": {r["run_id"]: r for r in runs}, "status": status}


def replines_check(bbg_dir: Path, isin: str, control_balance: float | None) -> dict:
    """Scaling factor control / sum(balance); Scaled Loan Level refused outside [0.95, 1.05]."""
    out = {}
    for kind in ("replines", "loanlevel"):
        p = Path(bbg_dir) / kind / f"{isin}.csv"
        rows, st = _read(p, REPLINE_COLS + (["loan_id"] if kind == "loanlevel" else []))
        if st != "ok":
            out[kind] = {"status": st}
            continue
        tot = sum(float(r["balance"] or 0) for r in rows)
        if not control_balance or tot <= 0:
            out[kind] = {"status": "UNUSABLE: no control balance", "sum": tot}
            continue
        f = control_balance / tot
        out[kind] = {"status": "ok" if 0.95 <= f <= 1.05 else f"REFUSED: scaling factor {f:.4f} outside [0.95,1.05]",
                     "sum": tot, "scaling_factor": f}
    return out
