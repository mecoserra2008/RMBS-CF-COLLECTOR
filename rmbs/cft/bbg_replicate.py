"""Replicate each captured Bloomberg CFT run with this engine and the run's own settings.

Two projections per run (bbg/runs.csv supplies cpr, cdr, severity, recovery_lag, call_assumption):
  engine_only : our engine, Bloomberg's settings, starting class-A balance = Bloomberg's own (first period ending
                balance + its principal) -> residual difference = waterfall / convention differences
  filed_state : same settings, starting from the last FILED IPD before the run's settlement date
                -> additional difference = Bloomberg's stale state (factor)
If engine_only reproduces Bloomberg to the cent, the conventions are identical and every remaining gap is state."""
from __future__ import annotations

import datetime as dt

import numpy as np

from ..history import fnum
from ..validate import MATCH_DAYS
from . import engine as E
from .grid import CALL, state_for

COLS = ["isin", "run_id", "variant", "n_dates", "max_abs_diff_eur", "mean_abs_diff_bp_orig", "start_balance_bbg",
        "start_balance_filed", "status"]


def _grid(run: dict) -> E.Grid:
    f = lambda k, d=0.0: float(run.get(k) or d)
    call = str(run.get("call_assumption", "")).lower()
    c = CALL["clean_up"] if "clean" in call or "call" in call and "no" not in call else CALL["maturity"]
    one = lambda v: np.array([v], dtype=float)
    return E.Grid(prepay_kind=one(0), prepay_level=one(f("cpr") / (100 if f("cpr") > 1 else 1)), default_kind=one(0),
                  default_level=one(f("cdr") / (100 if f("cdr") > 1 else 1)), severity=one(f("severity") / (100 if f("severity") > 1 else 1)),
                  lag=one(f("recovery_lag")), advancing=one(0), call=one(c), trigger=one(0), wac=one(0.0), wam=one(0.0),
                  index_shift=one(0.0))


def replicate(isin: str, rows: list[dict], deal: dict, bbg: dict) -> list[dict]:
    if bbg.get("status") != "ok":
        return [{"isin": isin, "status": bbg.get("status", "MANUAL_REQUIRED")}]
    runs = [r for r in bbg["runs"].values() if r["isin"] == isin]
    if not runs:
        return [{"isin": isin, "status": "MANUAL_REQUIRED: no Bloomberg run for this ISIN"}]
    out = []
    orig = float(deal.get("original_balance") or 1.0)
    for run in sorted(runs, key=lambda r: r["run_id"]):
        cfs = sorted((c for c in bbg["cashflows"] if c["run_id"] == run["run_id"]), key=lambda c: c["payment_date"])
        if not cfs:
            continue
        settle = dt.date.fromisoformat(run.get("settlement_date") or cfs[0]["payment_date"])
        k = max((i for i, r in enumerate(rows) if r["payment_date"] <= settle.isoformat()), default=None)
        st = state_for(rows, deal, upto=(k + 1) if k is not None else 0)
        if st is None:
            out.append({"isin": isin, "run_id": run["run_id"], "status": "NO_FILED_STATE before settlement"})
            continue
        g = _grid(run)
        g.wac[:] = fnum(rows[k], "wac") or 0.03
        g.wam[:] = 240.0
        p0 = float(cfs[0]["ending_balance"] or 0) + sum(float(cfs[0].get(x) or 0) for x in ("scheduled_principal", "prepayment", "default"))
        for variant, a0 in (("engine_only", p0), ("filed_state", st.a)):
            s2 = E.State(st.start, st.pool * (a0 / st.a if st.a else 1), a0, st.sub * (a0 / st.a if st.a else 1), st.pdl,
                         st.age0, st.index, st.prorata_filed, st.mode)
            pr = E.run(deal, s2, g, horizon_q=len(cfs) + 2)
            diffs = []
            for c in cfs:
                d = dt.date.fromisoformat(c["payment_date"])
                j = next((i for i, x in enumerate(pr["dates"]) if abs((x - d).days) <= MATCH_DAYS), None)
                if j is not None and c["ending_balance"]:
                    diffs.append(pr["a_end"][0, j] - float(c["ending_balance"]))
            out.append({"isin": isin, "run_id": run["run_id"], "variant": variant, "n_dates": len(diffs),
                        "max_abs_diff_eur": max((abs(x) for x in diffs), default=float("nan")),
                        "mean_abs_diff_bp_orig": (np.mean(np.abs(diffs)) / orig * 1e4) if diffs else float("nan"),
                        "start_balance_bbg": p0, "start_balance_filed": st.a,
                        "status": "ok" if diffs else "NO_MATCHING_DATES"})
    return out
