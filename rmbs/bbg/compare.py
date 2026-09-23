"""Score Bloomberg runs with the same metrics as the candidates, on the same realised filings."""
from __future__ import annotations

import datetime as dt

from ..history import fnum
from ..validate import MATCH_DAYS


def score_runs(isin: str, rows: list[dict], bbg: dict, champion_tab: dict | None, orig: float) -> list[dict]:
    if bbg["status"] != "ok":
        return [{"isin": isin, "metric": "all", "bbg": "", "model": "", "realised": "", "winner": "",
                 "status": bbg["status"]}]
    runs = [r for r in bbg["runs"].values() if r["isin"] == isin]
    if not runs:
        return [{"isin": isin, "metric": "all", "bbg": "", "model": "", "realised": "", "winner": "",
                 "status": "MANUAL_REQUIRED: no Bloomberg run for this ISIN"}]
    by = {dt.date.fromisoformat(r["payment_date"]): r for r in rows}
    out = []
    for run in sorted(runs, key=lambda r: r["run_id"]):
        cfs = sorted((c for c in bbg["cashflows"] if c["run_id"] == run["run_id"]), key=lambda c: c["payment_date"])
        errs = []
        for c in cfs:
            d = dt.date.fromisoformat(c["payment_date"])
            hit = [x for x in by if abs((x - d).days) <= MATCH_DAYS]
            if hit and fnum(by[hit[0]], "end_balance") is not None and c["ending_balance"]:
                errs.append(abs(float(c["ending_balance"]) - fnum(by[hit[0]], "end_balance")) / orig * 1e4)
        if not errs:
            out.append({"isin": isin, "metric": f"E_fac[{run['run_id']}]", "bbg": "", "model": "", "realised": "",
                        "winner": "", "status": "NO_OVERLAP: no realised IPD after the run date yet"})
            continue
        e_b = sum(errs) / len(errs)
        e_m = (champion_tab or {}).get("E_fac_h1")
        win = "" if e_m in (None, "") else ("model" if float(e_m) < e_b else "bbg")
        out.append({"isin": isin, "metric": f"E_fac_bp[{run['run_id']}]", "bbg": e_b, "model": e_m, "realised": 0.0,
                    "winner": win, "status": "ok"})
        # stale-factor check: Bloomberg's starting balance vs the filing at the run date
        first = cfs[0] if cfs else None
        if first:
            out.append({"isin": isin, "metric": f"start_balance[{run['run_id']}]", "bbg": float(first["ending_balance"] or 0),
                        "model": "", "realised": "", "winner": "", "status": "see docs/FINDINGS.md"})
    return out
