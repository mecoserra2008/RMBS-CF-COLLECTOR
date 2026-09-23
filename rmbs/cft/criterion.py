"""Does 'smoother' mean 'more correct'? Out-of-sample test of the smoothness criterion.

At each historical origin (after IPD k, using only filings published by then) every configuration is projected from
the filed state and scored for smoothness; the realised class-A balances published later are the truth. Reported per
origin: Spearman correlation between smoothness score and realised factor error (positive = smoother is closer),
error of the smoothest configuration, median error over the grid, error of the best configuration in hindsight, and
error of the naive 'last paydown rate held flat' projection."""
from __future__ import annotations

import datetime as dt

import numpy as np

from ..history import fnum
from ..selection.backtest import available_date, validated
from ..validate import MATCH_DAYS
from . import engine as E
from .grid import build, state_for
from .smooth import consistency, filed_rates, history_trend, score


def rankdata(a: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean rank), as in scipy.stats.rankdata."""
    order = np.argsort(a, kind="stable")
    sa = a[order]
    first = np.r_[True, sa[1:] != sa[:-1]]
    grp = np.cumsum(first) - 1
    starts = np.flatnonzero(first)
    ends = np.r_[starts[1:], len(a)]
    avg = (starts + ends - 1) / 2.0
    r = np.empty(len(a))
    r[order] = avg[grp]
    return r


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3:
        return float("nan")
    rx, ry = rankdata(x[m]), rankdata(y[m])
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def naive_grid(g: E.Grid, rate: float) -> E.Grid:
    one = g.take(np.array([0]))
    one.prepay_kind[:] = 0
    one.prepay_level[:] = min(max(rate, 0.0), 0.99)
    one.default_level[:] = 0.0
    one.call[:] = 0
    one.trigger[:] = 0
    return one


def check(isin: str, rows: list[dict], deal: dict, cfg: dict, min_hist: int = 3) -> list[dict]:
    rows = [r for r in validated(rows) if fnum(r, "beg_balance")]
    out = []
    orig = float(deal.get("original_balance") or 1.0)
    for k in range(min_hist - 1, len(rows) - 1):
        cut = available_date(rows[k], deal)
        info = [r for r in rows if available_date(r, deal) <= cut]
        targets = [r for r in rows if available_date(r, deal) > cut and r["payment_date"] > info[-1]["payment_date"]]
        if len(info) < min_hist or not targets:
            continue
        st = state_for(info, deal)
        tr = history_trend(info, cfg["smoothness"]["history_points"])
        if st is None or tr is None:
            continue
        g = build(cfg, deal, st, info)
        last_t = dt.date.fromisoformat(targets[0]["payment_date"])
        hq = max(cfg["horizon_quarters"], int((last_t - st.start).days / 91) + 2)
        pr = E.run(deal, st, g, horizon_q=hq)
        sc = score(pr, st.a, tr, cfg["smoothness"]["weights"])
        sc["S"] = sc["S"] + consistency(g, st.age0, filed_rates(info), cfg.get("consistency", {}).get("weights", {}))["C"]
        dates = pr["dates"]
        t = targets[0]
        td = dt.date.fromisoformat(t["payment_date"])
        j = next((i for i, d in enumerate(dates) if abs((d - td).days) <= MATCH_DAYS), None)
        if j is None:
            continue
        err = np.abs(pr["a_end"][:, j] - fnum(t, "end_balance")) / orig * 1e4
        best = int(np.argmin(sc["S"]))
        m = 1 - (1 - fnum(info[-1], "principal_paid") / fnum(info[-1], "beg_balance")) ** 4
        nv = E.run(deal, st, naive_grid(g, m), horizon_q=j + 1)
        nerr = abs(nv["a_end"][0, j] - fnum(t, "end_balance")) / orig * 1e4
        top = sc["S"] <= np.quantile(sc["S"], 0.01)
        out.append({"isin": isin, "origin_ipd": info[-1]["payment_date"], "target_ipd": t["payment_date"],
                    "steps_ahead": j + 1, "n_configs": len(g), "state_mode": st.mode,
                    "spearman_S_vs_error": spearman(sc["S"], err), "err_smoothest_bp": float(err[best]),
                    "err_top1pct_mean_bp": float(err[top].mean()), "err_grid_median_bp": float(np.median(err)),
                    "err_hindsight_best_bp": float(err.min()), "err_naive_last_rate_bp": float(nerr)})
    return out
