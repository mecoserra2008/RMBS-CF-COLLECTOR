"""Composite score, acceptance gates A1-A7, champion selection, fan chart."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
from statistics import fmean

from ..config import ipd_rules
from ..engine import project
from ..history import fnum
from . import bootstrap as B
from .backtest import (HORIZONS, Context, assumptions_for, forecast_at, index_ref, observations, origins,
                       origins_thin, run_origin, validated)
from .candidates import CPR_MODELS, Candidate, grid, key_param
from .metrics import nanmean, rmse, stab, zscores

DEFAULT_WEIGHTS = {"E_dm": 0.30, "E_wal": 0.20, "E_prin": 0.20, "E_cum": 0.15, "CRPS": 0.10, "STAB": 0.05}
DEFAULT_GATES = {"A2_U_max": 1.0, "A3_E_fac_h1_bp_max": 25, "A4_E_dm_h4_bp_max": 10, "A5_coverage": [0.72, 0.88],
                 "A6_win_share_min": 0.60}
DEFAULT_PENALTIES = {"U_ge_1": 0.50, "cov_off": 0.30, "bias": 0.25, "per_param_over_2": 0.10}


def summarise(results: list[dict], naive: list[dict] | None) -> dict:
    """Per-horizon metric table for one candidate. ``naive`` = cpr_last results on the same origins (for U)."""
    out = {}
    nb = {(r["cut"], h): r["h"][h] for r in (naive or []) for h in r["h"]}
    for h in HORIZONS:
        recs = [(r["cut"], r["h"][h]) for r in results if h in r["h"]]
        if not recs:
            continue
        pr = [x["prin_real"] for _, x in recs]
        mp = fmean(pr) if pr else float("nan")
        errs = [x["bal_err"] for _, x in recs]
        nerr = [nb[(c, h)]["bal_err"] for c, _ in recs if (c, h) in nb]
        rn = rmse(nerr) if len(nerr) == len(errs) else float("nan")
        cum = [abs(x["cum_pred"] - x["cum_real"]) / x["cum_real"] for _, x in recs if x["cum_real"] > 0]
        out[h] = {"n_obs": len(recs),
                  "E_fac": nanmean([x["fac_err"] for _, x in recs]),
                  "E_prin": nanmean([abs(x["prin_pred"] - x["prin_real"]) for _, x in recs]) / mp if mp else float("nan"),
                  "E_cum": nanmean(cum),
                  "E_wal": nanmean([x.get("wal_err", float("nan")) for _, x in recs]),
                  "E_dm": nanmean([x.get("dm_err", float("nan")) for _, x in recs]),
                  "U": (rmse(errs) / rn) if rn and not math.isnan(rn) and rn > 0 else (1.0 if rn == 0 and rmse(errs) == 0 else float("nan")),
                  "CRPS": nanmean([x["crps"] for _, x in recs]),
                  "COV": fmean([1.0 if x["in_band"] else 0.0 for _, x in recs]),
                  "BIAS": (fmean([x["prin_pred"] - x["prin_real"] for _, x in recs]) / mp) if mp else float("nan")}
    return out


def pick(tab: dict, h: int, k: str) -> float:
    return tab.get(h, {}).get(k, float("nan"))


def evaluate_isin(isin: str, rows: list[dict], deal: dict, pooled_obs, gate_status: str, cfg: dict) -> dict:
    """Run the full selection for one ISIN. Returns {scores: [...], champion: {...}, fan: [...], status}."""
    v = validated(rows)
    n = len(v)
    sc = cfg.get("scoring", {})
    weights = sc.get("weights", DEFAULT_WEIGHTS)
    pen = sc.get("penalties", DEFAULT_PENALTIES)
    fitted_at = str(cfg.get("valuation_date"))
    base = {"isin": isin, "fitted_at": fitted_at}
    if not ipd_rules(deal) or not deal.get("original_balance"):
        return {"status": "NO_RELIABLE_MODEL: CONFIG_INCOMPLETE (ipd_rule/original_balance)", "scores": [], "fan": [],
                "champion": {**base, "status": "NO_RELIABLE_MODEL", "gates_passed": "CONFIG_INCOMPLETE"}}
    if n < 3:
        return {"status": "NO_RELIABLE_MODEL: NO_HISTORY (<3 validated IPDs)", "scores": [], "fan": [],
                "champion": {**base, "status": "NO_RELIABLE_MODEL", "gates_passed": f"NO_HISTORY ({n} validated IPDs)"}}
    thin = n < 12
    obs = observations(isin, rows, deal)
    ctx = Context(deal=deal, rows=v, pooled=pooled_obs, price_ref=float(sc.get("price_ref", 98.5)),
                  draws=int(sc.get("draws_backtest", 200)), seed=int(cfg.get("seed", 20260923)))
    cuts = origins_thin(rows, deal) if thin else origins(rows, deal)
    cands = grid(deal, thin)
    per_c = {}
    for c in cands:
        res = [x for x in (run_origin(c, ctx, obs, cut) for cut in cuts) if x]
        per_c[c] = res
    naive_c = Candidate("cpr_last", "cdr_zero", "rec_none", "call_never")
    if naive_c not in per_c:
        per_c_naive = [x for x in (run_origin(naive_c, ctx, obs, cut) for cut in cuts) if x]
    else:
        per_c_naive = per_c[naive_c]
    tabs = {c: summarise(r, per_c_naive) for c, r in per_c.items() if r}
    live = [c for c in cands if c in tabs]
    if not live:
        return {"status": "NO_RELIABLE_MODEL: no origin with a realised target", "scores": [], "fan": [],
                "champion": {**base, "status": "NO_RELIABLE_MODEL", "gates_passed": "NO_ORIGINS"}}
    # --- per-origin winners (A6)
    wins = {c: 0 for c in live}
    all_cuts = sorted({r["cut"] for c in live for r in per_c[c] if 1 in r["h"]})
    for cut in all_cuts:
        errs = {c: next((r["h"][1]["fac_err"] for r in per_c[c] if r["cut"] == cut and 1 in r["h"]), None) for c in live}
        errs = {c: e for c, e in errs.items() if e is not None}
        if errs:
            m = min(errs.values())
            for c, e in errs.items():
                if e <= m + 1e-9:
                    wins[c] += 1
    # --- composite score
    metric_at = {"E_dm": 4, "E_wal": 4, "E_prin": 1, "E_cum": 8, "CRPS": 1}
    cols = {}
    for k, h in metric_at.items():
        # no fallback: a metric whose horizon has no complete realised window is dropped and the weights renormalised
        cols[k] = [pick(tabs[c], h, k) for c in live]
    stabs = [stab([key_param(c.cpr, r["key"]) for r in per_c[c]]) for c in live]
    cols["STAB"] = stabs
    used = {k: w for k, w in weights.items() if not all(math.isnan(x) for x in cols[k])}
    tw = sum(used.values()) or 1.0
    zs = {k: zscores(cols[k]) for k in used}
    S = []
    for i, c in enumerate(live):
        s = sum(used[k] / tw * zs[k][i] for k in used)
        t = tabs[c]
        u1 = pick(t, 1, "U")
        if not (u1 < 1):
            s += pen["U_ge_1"]
        cov = pick(t, 1, "COV")
        if math.isnan(cov) or abs(cov - 0.80) > 0.15:
            s += pen["cov_off"]
        b = pick(t, 1, "BIAS")
        if math.isnan(b) or abs(b) > 0.10:
            s += pen["bias"]
        s += pen["per_param_over_2"] * max(0, c.n_params - 2)
        S.append(s)
    order = sorted(range(len(live)), key=lambda i: (round(S[i], 12), live[i].n_params, live[i].name))
    best = order[0]
    for i in order[1:]:
        if S[i] - S[best] < 0.05 and (live[i].n_params, _nz(pick(tabs[live[i]], 4, "E_dm"))) < \
                (live[best].n_params, _nz(pick(tabs[live[best]], 4, "E_dm"))):
            best = i
    champ = live[best]
    t = tabs[champ]
    n_or = len(all_cuts)
    share = wins[champ] / n_or if n_or else 0.0
    leak_ok = leakage_check(champ, ctx, obs, cuts[:3])
    gt = {**DEFAULT_GATES, **sc.get("gates", {})}
    gates = {"A1": gate_status == "PASS",
             "A2": pick(t, 1, "U") < gt["A2_U_max"] and pick(t, 4, "U") < gt["A2_U_max"],
             "A3": pick(t, 1, "E_fac") <= gt["A3_E_fac_h1_bp_max"],
             "A4": pick(t, 4, "E_dm") <= gt["A4_E_dm_h4_bp_max"],
             "A5": gt["A5_coverage"][0] <= pick(t, 1, "COV") <= gt["A5_coverage"][1],
             "A6": share >= gt["A6_win_share_min"],
             "A7": leak_ok}
    failed = [k for k, ok in gates.items() if not ok]
    status = "CHAMPION" if not failed else "NO_RELIABLE_MODEL"
    params = [r["key"] for r in per_c[champ]][-1] if per_c[champ] else {}
    scores = []
    for i, c in enumerate(live):
        for h, m in sorted(tabs[c].items()):
            scores.append({"isin": isin, "candidate": c.name, "cpr_model": c.cpr, "cdr_model": c.cdr,
                           "recovery": c.recovery, "call": c.call, "n_params": c.n_params, "h": h, **m,
                           "STAB": stabs[i], "S": S[i], "win_share": wins[c] / n_or if n_or else 0.0})
    champion = {**base, "cpr_model": champ.cpr, "cdr_model": champ.cdr + (" (bond-level: defaults embedded in paydown rate)" if champ.cdr == "cdr_zero" else ""),
                "recovery": champ.recovery, "call": champ.call, "params_json": json.dumps(_r(params), sort_keys=True),
                "S": S[best], "U_h1": pick(t, 1, "U"), "E_dm_h4": pick(t, 4, "E_dm"), "E_fac_h1": pick(t, 1, "E_fac"),
                "coverage": pick(t, 1, "COV"), "n_origins": n_or,
                "gates_passed": ",".join(k for k, ok in gates.items() if ok) + ((" | failed " + ",".join(failed)) if failed else ""),
                "status": status + (" (THIN_HISTORY)" if thin else "")}
    fan = fan_chart(isin, champ, v, obs, pooled_obs, deal, cfg)
    return {"status": status + (": failed " + ",".join(failed) if failed else ""), "scores": scores,
            "champion": champion, "fan": fan, "champion_candidate": champ, "thin": thin}


def _nz(x):
    return 1e9 if math.isnan(x) else x


def _r(d):
    return {k: (round(v, 8) if isinstance(v, float) else v) for k, v in d.items()}


def leakage_check(c: Candidate, ctx: Context, obs, cuts) -> bool:
    """A7 at runtime: scrambling every row published after the cut must not move the forecast made at the cut."""
    for cut in cuts:
        f0 = forecast_at(c, ctx.rows, obs, ctx.pooled, ctx.deal, cut)
        if f0 is None:
            continue
        rows2 = copy.deepcopy(ctx.rows)
        from .backtest import available_date
        for r in rows2:
            if available_date(r, ctx.deal) > cut:
                for k in ("beg_balance", "principal_paid", "end_balance"):
                    if fnum(r, k) is not None:
                        r[k] = fnum(r, k) * 1.37
        obs2 = [o if dt.date.fromisoformat(o.available) <= cut else type(o)(o.date, o.available, min(o.rate * 2 + 0.05, 0.9), o.age, o.coupon, o.isin) for o in obs]
        pooled2 = [o if dt.date.fromisoformat(o.available) <= cut else type(o)(o.date, o.available, 0.5, o.age, o.coupon, o.isin) for o in ctx.pooled]
        f1 = forecast_at(c, rows2, obs2, pooled2, ctx.deal, cut)
        if f1 is None or [p.end for p in f0[2].periods] != [p.end for p in f1[2].periods]:
            return False
    return True


def fan_chart(isin, c: Candidate, rows, obs, pooled, deal, cfg) -> list[dict]:
    """p5..p95 of principal, balance, DM by IPD from the last validated IPD (block bootstrap, >= 1,000 draws)."""
    sc = cfg.get("scoring", {})
    draws = int(sc.get("draws_fan", 1000))
    steps = int(sc.get("fan_steps", 40))
    price = float(sc.get("price_ref", 98.5))
    state = rows[-1]
    params = CPR_MODELS[c.cpr].fit(obs, {"pooled_obs": pooled, "step_months": 12.0 / len(ipd_rules(deal)[-1]["months"])})
    if params is None:
        return []
    idx, _ = index_ref(state, deal)
    start = dt.date.fromisoformat(state["payment_date"])
    B0 = fnum(state, "end_balance")
    from .backtest import one_step_residuals
    res = one_step_residuals(c, obs, pooled, deal)
    paths = B.block_paths(res, steps, draws, int(cfg.get("seed", 20260923)))
    path = CPR_MODELS[c.cpr].path
    bal, prin, dms, dates = [], [], [], None
    from ..engine import cashflows, solve_dm
    for ep in paths:
        a = assumptions_for(c, params, deal, state, idx)
        if a is None:
            return []
        a.rate_path = (lambda k, ep=ep: B.perturb(min(max(path(params, k), 1e-6), 0.99), ep[k - 1]))
        pj = project(deal, start, B0, a, steps)
        pe = pj.periods
        dates = dates or [p.date for p in pe]
        b = [p.end for p in pe] + [pe[-1].end if pe else B0] * (steps - len(pe))
        pr = [p.principal for p in pe] + [0.0] * (steps - len(pe))
        bal.append(b)
        prin.append(pr)
        cfs = cashflows(pj)
        if cfs and pe[-1].end > 0.005:                      # truncated at the fan horizon: residual as a bullet
            d, amt, p_, i_ = cfs[-1]
            cfs[-1] = (d, amt + pe[-1].end, p_ + pe[-1].end, i_)
        dms.append(solve_dm(cfs, start, idx, price / 100 * B0) * 1e4 if cfs else float("nan"))
    full_dates = dates or []
    if len(full_dates) < steps:
        from .. import calendar as cal
        full_dates = cal.next_ipds(ipd_rules(deal), start, steps)
    out = []
    for k in range(steps):
        for name, arr in (("principal", [p[k] for p in prin]), ("balance", [b[k] for b in bal])):
            out.append({"isin": isin, "payment_date": full_dates[k].isoformat(), "step": k + 1, "metric": name,
                        **{f"p{int(q * 100)}": B.quantile(arr, q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}})
    out.append({"isin": isin, "payment_date": "", "step": 0, "metric": "dm_bp_at_price_ref",
                **{f"p{int(q * 100)}": B.quantile([d for d in dms if not math.isnan(d)], q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}})
    return out
