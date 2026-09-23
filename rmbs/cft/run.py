"""CFT grid stage: rank every configuration per ISIN by smoothness, report the top-N with full-life cash-flow
metrics, per-dimension marginals, and the out-of-sample criterion check."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

from .. import config as C
from ..engine import solve_dm
from ..schema import write_table
from ..selection.backtest import validated
from . import engine as E
from .criterion import check
from .grid import build, describe, state_for
from .smooth import consistency, filed_rates, history_trend, score

RANK_COLS = ["isin", "rank", "S", "S_smooth", "C_filing", "cpr_cfg_initial", "cdr_cfg_initial", "seam_level", "seam_slope", "roughness", "terminal", "prepay", "default", "severity",
             "lag_m", "advancing", "call", "trigger", "wac", "wam_m", "index_shift", "first_ipd_rate_annual",
             "wal_years", "dm_bp_at_ref", "last_cf_date", "state_mode"]
SUMMARY_COLS = ["isin", "state_mode", "n_configs", "best_S", "best_config", "equivalence_class_size",
                "equiv_rate_min", "equiv_rate_max", "equiv_wal_min", "equiv_wal_max", "hist_last_rate_annual", "filed_facts_used",
                "criterion_origins", "criterion_mean_spearman", "criterion_smoothest_beats_naive", "status"]
MARG_COLS = ["isin", "dimension", "value", "best_S", "median_S", "n"]
CRIT_COLS = ["isin", "origin_ipd", "target_ipd", "steps_ahead", "n_configs", "state_mode", "spearman_S_vs_error",
             "err_smoothest_bp", "err_top1pct_mean_bp", "err_grid_median_bp", "err_hindsight_best_bp", "err_naive_last_rate_bp"]


def _tables():
    from ..schema import TABLES
    from .bbg_replicate import COLS
    TABLES.setdefault("cft_bbg_replication", COLS)
    TABLES.setdefault("cft_ranking", RANK_COLS)
    TABLES.setdefault("cft_summary", SUMMARY_COLS)
    TABLES.setdefault("cft_marginals", MARG_COLS)
    TABLES.setdefault("cft_criterion", CRIT_COLS)


def rank_isin(isin: str, rows: list[dict], deal: dict, cfg: dict, price_ref: float):
    rows = validated(rows)
    st = state_for(rows, deal)
    tr = history_trend(rows, cfg["smoothness"]["history_points"])
    if st is None or tr is None or not C.ipd_rules(deal):
        return None
    g = build(cfg, deal, st, rows)
    pr = E.run(deal, st, g, horizon_q=cfg["horizon_quarters"])
    sc = score(pr, st.a, tr, cfg["smoothness"]["weights"])
    filed = filed_rates(rows)
    cs = consistency(g, st.age0, filed, cfg.get("consistency", {}).get("weights", {}))
    sc["S_smooth"] = sc["S"]
    sc["S"] = sc["S_smooth"] + cs["C"]
    order = np.lexsort((np.arange(len(g)), np.round(sc["S"], 12)))
    top = order[: cfg["top_n_output"]]
    full = E.run(deal, st, g.take(top))                        # full life for the top-N
    ranking = []
    for i, k in enumerate(top):
        cf = [(d, p + it, p, it) for d, p, it in zip(full["dates"], full["a_prin"][i], full["a_int"][i]) if p + it > 0]
        wal = sum((d - st.start).days / 365.0 * p for d, _, p, _ in cf) / max(sum(p for _, _, p, _ in cf), 1e-9)
        dm = solve_dm(cf, st.start, st.index, price_ref / 100 * st.a) * 1e4 if cf else float("nan")
        ranking.append({"isin": isin, "rank": i + 1, "S": sc["S"][k], "S_smooth": sc["S_smooth"][k],
                        "C_filing": cs["C"][k], "cpr_cfg_initial": cs["cpr_cfg"][k], "cdr_cfg_initial": cs["cdr_cfg"][k],
                        "seam_level": sc["seam_level"][k],
                        "seam_slope": sc["seam_slope"][k], "roughness": sc["roughness"][k], "terminal": sc["terminal"][k],
                        **describe(g, k), "first_ipd_rate_annual": sc["rho1_annual"][k], "wal_years": wal,
                        "dm_bp_at_ref": dm, "last_cf_date": cf[-1][0].isoformat() if cf else "", "state_mode": st.mode})
    best = sc["S"][order[0]]
    eq = sc["S"] <= best + cfg["smoothness"]["equivalence_delta"]
    eq_top = [r for r, k in zip(ranking, top) if eq[k]]
    marg = []
    dims = {"prepay": lambda k: describe(g, k)["prepay"].split()[0], "prepay_setting": lambda k: describe(g, k)["prepay"],
            "default": lambda k: describe(g, k)["default"].split()[0], "severity": lambda k: g.severity[k],
            "lag_m": lambda k: int(g.lag[k]), "advancing": lambda k: int(g.advancing[k]),
            "call": lambda k: describe(g, k)["call"], "trigger": lambda k: describe(g, k)["trigger"],
            "wac": lambda k: g.wac[k], "wam_m": lambda k: g.wam[k]}
    for dim, f in dims.items():
        vals = {}
        for k in range(len(g)):
            vals.setdefault(f(k), []).append(sc["S"][k])
        for v in sorted(vals, key=str):
            a = np.asarray(vals[v])
            marg.append({"isin": isin, "dimension": dim, "value": v, "best_S": a.min(), "median_S": float(np.median(a)), "n": len(a)})
    last_rate = 1 - (1 - rows[-1]["principal_paid"] / rows[-1]["beg_balance"]) ** 4
    summary = {"isin": isin, "state_mode": st.mode, "n_configs": len(g), "best_S": best,
               "best_config": "; ".join(f"{k}={v}" for k, v in describe(g, order[0]).items()),
               "equivalence_class_size": int(eq.sum()),
               "equiv_rate_min": float(sc["rho1_annual"][eq].min()), "equiv_rate_max": float(sc["rho1_annual"][eq].max()),
               "equiv_wal_min": min((r["wal_years"] for r in eq_top), default=float("nan")),
               "equiv_wal_max": max((r["wal_years"] for r in eq_top), default=float("nan")),
               "hist_last_rate_annual": last_rate,
               "filed_facts_used": ",".join(cs["identified"]) or "none (smoothness only: prepay/default split not identified)"}
    return ranking, marg, summary


def run_all(root: Path, isins: list[str], cfg_run: dict) -> str:
    _tables()
    cfg = C.load_yaml(root / "config" / "cft.yaml")
    price = float(cfg_run.get("scoring", {}).get("price_ref", 98.5))
    out = root / cfg_run.get("out_dir", "out") / "cft"
    out.mkdir(parents=True, exist_ok=True)
    from .. import history as Hs
    sums, crit_all = [], []
    for isin in isins:
        deal = C.load_deal(isin, root / "config" / "deals")
        rows = Hs.load(root / cfg_run.get("out_dir", "out"), isin)
        res = rank_isin(isin, rows, deal, cfg, price) if rows else None
        if res is None:
            sums.append({"isin": isin, "status": "NO_STATE (no validated history or IPD rule)"})
            continue
        ranking, marg, summary = res
        crit = check(isin, rows, deal, cfg)
        crit_all += crit
        sp = [c["spearman_S_vs_error"] for c in crit if np.isfinite(c["spearman_S_vs_error"])]
        beats = [c["err_smoothest_bp"] < c["err_naive_last_rate_bp"] for c in crit]
        summary.update(criterion_origins=len(crit), criterion_mean_spearman=float(np.mean(sp)) if sp else float("nan"),
                       criterion_smoothest_beats_naive=(f"{sum(beats)}/{len(beats)}" if beats else ""),
                       status="RANKED" + ("" if summary["state_mode"] == "pool" else f" ({summary['state_mode']}: repline inputs assumed)"))
        sums.append(summary)
        write_table(out / f"{isin}_ranking.csv", "cft_ranking", ranking)
        write_table(out / f"{isin}_marginals.csv", "cft_marginals", marg)
    from ..bbg import ingest
    from .bbg_replicate import replicate
    bbg = ingest.load(root / "bbg")
    rep = []
    for isin in isins:
        rows = Hs.load(root / cfg_run.get("out_dir", "out"), isin)
        rep += replicate(isin, rows, C.load_deal(isin, root / "config" / "deals"), bbg)
    write_table(out / "bbg_replication.csv", "cft_bbg_replication", rep)
    write_table(out / "summary.csv", "cft_summary", sums)
    write_table(out / "criterion_check.csv", "cft_criterion", crit_all)
    n = sum(1 for s in sums if str(s.get("status", "")).startswith("RANKED"))
    sp = [c["spearman_S_vs_error"] for c in crit_all if np.isfinite(c["spearman_S_vs_error"])]
    return f"{n} ISINs ranked; criterion check on {len(crit_all)} origins, mean Spearman(S, error) = " + \
        (f"{np.mean(sp):+.2f}" if sp else "n/a")
