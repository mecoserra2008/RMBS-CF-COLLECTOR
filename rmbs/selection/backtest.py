"""Rolling-origin, strictly causal backtest.

Origin = a cut date. The information set at a cut is every validated row whose *publication* date (available) is on or
before the cut -- annual-accounts rows only become available after the accounts are filed. Every fit and every
forecast is a function of the information set alone (``forecast_at``), which is what the leakage tests perturb.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from .. import calendar as cal
from ..collateral import annual_from_period
from ..config import ipd_rules
from ..engine import Assumptions, project, pv, solve_dm
from ..history import fnum, is_kEUR
from ..validate import MATCH_DAYS
from . import bootstrap as B
from .candidates import CPR_MODELS, Candidate, Obs

HORIZONS = (1, 4, 8, 12)


def available_date(r: dict, deal: dict) -> dt.date:
    d = dt.date.fromisoformat(r["payment_date"])
    if is_kEUR(r):                               # audited accounts: filed by 30 April of the following year
        return dt.date(d.year + 1, 4, 30)
    lag = deal.get("publication_lag_days")
    return d + dt.timedelta(days=int(lag if lag is not None else 30))


def step_months(deal: dict) -> float:
    rules = ipd_rules(deal)
    return 12.0 / len(rules[-1]["months"]) if rules else 3.0


def validated(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("parse_status", "")).startswith("ok")
            and fnum(r, "beg_balance") and fnum(r, "principal_paid") is not None and fnum(r, "end_balance") is not None]


def observations(isin: str, rows: list[dict], deal: dict) -> list[Obs]:
    rows = validated(rows)
    if not rows:
        return []
    m = step_months(deal)
    first = dt.date.fromisoformat(str(deal.get("first_ipd") or rows[0]["payment_date"]))
    out = []
    for r in rows:
        d = dt.date.fromisoformat(r["payment_date"])
        rate = annual_from_period(fnum(r, "principal_paid") / fnum(r, "beg_balance"), m)
        out.append(Obs(r["payment_date"], available_date(r, deal).isoformat(), rate,
                       max(0.0, (d - first).days / 30.4375), fnum(r, "coupon_rate"), isin))
    return out


@dataclass
class Context:
    deal: dict
    rows: list[dict]                       # validated rows of this ISIN (targets + state)
    pooled: list[Obs] = field(default_factory=list)   # other ISINs' observations (for pooled / season)
    price_ref: float = 98.5
    draws: int = 200
    seed: int = 20260923


def info_rows(rows: list[dict], deal: dict, cut: dt.date) -> list[dict]:
    return [r for r in rows if available_date(r, deal) <= cut]


def fit_candidate(c: Candidate, obs: list[Obs], ctx_extra: dict):
    return CPR_MODELS[c.cpr].fit(obs, ctx_extra)


def assumptions_for(c: Candidate, params: dict, deal: dict, state_row: dict, index: float) -> Assumptions | None:
    path = CPR_MODELS[c.cpr].path
    a = Assumptions(rate_path=lambda k: min(max(path(params, k), 0.0), 0.99), index=index, call=c.call)
    if c.call == "call_issuer_stated":
        a.call_date = dt.date.fromisoformat(str(deal["call_issuer_stated"]))
    if c.call == "call_at_threshold":
        pool = fnum(state_row, "coll_end_balance_net") or fnum(state_row, "coll_end_balance")
        bond = fnum(state_row, "end_balance")
        pac, pct, orig = deal.get("pool_at_closing"), deal.get("clean_up_call_pct"), deal.get("original_balance")
        if not (pool and bond and pac and pct and orig):
            return None                                   # threshold not observable -> candidate not identifiable
        a.call_factor = float(pct) * float(pac) / (pool / bond) / float(orig)
    return a


def index_ref(state_row: dict, deal: dict) -> tuple[float, float]:
    """(index, coupon) as known at the state row: realised coupon if filed; margin-only otherwise (recorded)."""
    cpn = fnum(state_row, "coupon_rate")
    m = deal.get("margin_bp")
    idx = fnum(state_row, "index_rate")
    if idx is None:
        idx = (cpn - float(m) / 1e4) if (cpn is not None and m is not None) else 0.0
    return idx, (cpn if cpn is not None else idx + (float(m) / 1e4 if m is not None else 0.0))


def forecast_at(c: Candidate, rows: list[dict], obs: list[Obs], pooled: list[Obs], deal: dict, cut: dt.date,
                n_steps: int = 12):
    """Everything the origin at ``cut`` may know -> (params, state_row, projection) or None."""
    info = info_rows(rows, deal, cut)
    if not info:
        return None
    iobs = [o for o in obs if dt.date.fromisoformat(o.available) <= cut]
    ipool = [o for o in pooled if dt.date.fromisoformat(o.available) <= cut]
    params = CPR_MODELS[c.cpr].fit(iobs, {"pooled_obs": ipool, "step_months": step_months(deal)})
    if params is None:
        return None
    state = info[-1]
    idx, _ = index_ref(state, deal)
    a = assumptions_for(c, params, deal, state, idx)
    if a is None:
        return None
    proj = project(deal, dt.date.fromisoformat(state["payment_date"]), fnum(state, "end_balance"), a, n_steps)
    return params, state, proj, iobs


def realised_path(rows: list[dict], deal: dict, start: dt.date, n: int = 12) -> list[dict | None]:
    """Realised rows at the next n calendar IPDs after ``start`` (None where no filing is held)."""
    exp = cal.next_ipds(ipd_rules(deal), start, n)
    by = {dt.date.fromisoformat(r["payment_date"]): r for r in rows}
    out = []
    for e in exp:
        hit = [d for d in by if abs((d - e).days) <= MATCH_DAYS]
        out.append(by[hit[0]] if hit else None)
    return out


def one_step_residuals(c: Candidate, iobs: list[Obs], ipool: list[Obs], deal: dict, min_train: int = 3) -> list[float]:
    """logit(r_i) - logit(forecast of r_i from iobs[:i]); falls back to deviations from the mean when the candidate
    cannot be fitted on short prefixes (fewer than 3 residuals)."""
    from ..collateral import logit
    m = CPR_MODELS[c.cpr]
    out = []
    for i in range(min_train, len(iobs)):
        cut_i = iobs[i].date
        p = m.fit(iobs[:i], {"pooled_obs": [o for o in ipool if o.available <= cut_i], "step_months": step_months(deal)})
        if p is None:
            continue
        f = min(max(m.path(p, 1), 1e-6), 0.99)
        out.append(logit(iobs[i].rate) - logit(f))
    if len(out) < 3:
        rates = [o.rate for o in iobs[-12:]]
        mu = sum(rates) / len(rates) if rates else 0.05
        out = B.residuals(rates, min(max(mu, 1e-6), 0.99))
    return out[-24:]


def window_dm(beg: float, prins: list[float], dates: list[dt.date], start: dt.date, cpn: float, idx: float,
              price: float) -> float:
    """DM of a truncated window: interest at the reference coupon, principal, bullet of the residual at the end."""
    cfs, bal, prev = [], beg, start
    for i, (p, d) in enumerate(zip(prins, dates)):
        intr = bal * cpn * (d - prev).days / 360.0
        bal -= p
        amt = p + intr + (bal if i == len(prins) - 1 else 0.0)
        cfs.append((d, amt, p, intr))
        prev = d
    return solve_dm(cfs, start, idx, price / 100.0 * beg)


def window_wal(beg: float, prins: list[float], dates: list[dt.date], start: dt.date) -> float:
    bal, num = beg, 0.0
    for i, (p, d) in enumerate(zip(prins, dates)):
        t = (d - start).days / 365.0
        bal -= p
        num += t * (p + (bal if i == len(prins) - 1 else 0.0))
    return num / beg


def run_origin(c: Candidate, ctx: Context, obs: list[Obs], cut: dt.date) -> dict | None:
    f = forecast_at(c, ctx.rows, obs, ctx.pooled, ctx.deal, cut)
    if f is None:
        return None
    params, state, proj, iobs = f
    start = dt.date.fromisoformat(state["payment_date"])
    B0 = fnum(state, "end_balance")
    real = realised_path(ctx.rows, ctx.deal, start, 12)
    if not any(real):
        return None
    orig = float(ctx.deal.get("original_balance") or B0)
    idx, cpn = index_ref(state, ctx.deal)
    per = proj.periods
    pred_bal = [p.end for p in per] + [per[-1].end if per else B0] * (12 - len(per))
    pred_prin = [p.principal for p in per] + [0.0] * (12 - len(per))
    dates = cal.next_ipds(ipd_rules(ctx.deal), start, 12)
    # predictive ensemble: block bootstrap of the candidate's own rolling one-step residuals (information set only)
    res = one_step_residuals(c, iobs, [o for o in ctx.pooled if dt.date.fromisoformat(o.available) <= cut], ctx.deal)
    ens_paths = B.block_paths(res, 12, ctx.draws, ctx.seed + int(cut.strftime("%Y%m%d")))
    ens_bal = []
    for ep in ens_paths:
        a = assumptions_for(c, params, ctx.deal, state, idx)
        path = CPR_MODELS[c.cpr].path
        a.rate_path = (lambda k, ep=ep: B.perturb(min(max(path(params, k), 1e-6), 0.99), ep[k - 1]))
        pj = project(ctx.deal, start, B0, a, 12)
        eb = [p.end for p in pj.periods] + [pj.periods[-1].end if pj.periods else B0] * (12 - len(pj.periods))
        ens_bal.append(eb)
    out = {"cut": cut.isoformat(), "start": state["payment_date"], "key": params, "h": {}}
    for h in HORIZONS:
        r = real[h - 1]
        if r is None:
            continue
        rb = fnum(r, "end_balance")
        rp = fnum(r, "principal_paid")
        ens_h = [e[h - 1] for e in ens_bal]
        rec = {"fac_err": abs(pred_bal[h - 1] - rb) / orig * 1e4, "prin_pred": pred_prin[h - 1], "prin_real": rp,
               "bal_err": pred_bal[h - 1] - rb, "cum_pred": B0 - pred_bal[h - 1], "cum_real": B0 - rb,
               "crps": B.crps(ens_h, rb) / B0 if B0 else float("nan"),
               "in_band": B.quantile(ens_h, 0.10) <= rb <= B.quantile(ens_h, 0.90)}
        if h > 1 and all(real[k] is not None for k in range(h)):   # h=1 windows are degenerate (WAL = t1)
            rprins = [fnum(real[k], "principal_paid") for k in range(h)]
            rec["wal_err"] = abs(window_wal(B0, pred_prin[:h], dates[:h], start) - window_wal(B0, rprins, dates[:h], start))
            dp = window_dm(B0, pred_prin[:h], dates[:h], start, cpn, idx, ctx.price_ref)
            dr = window_dm(B0, rprins, dates[:h], start, cpn, idx, ctx.price_ref)
            rec["dm_err"] = abs(dp - dr) * 1e4 if not (math.isnan(dp) or math.isnan(dr)) else float("nan")
        out["h"][h] = rec
    return out


def origins(rows: list[dict], deal: dict) -> list[dt.date]:
    """Cuts: the day before each validated IPD after the warm-up (T_i[warmup:])."""
    v = validated(rows)
    n = len(v)
    warm = max(8, math.ceil(0.4 * n))
    cuts = sorted({dt.date.fromisoformat(r["payment_date"]) - dt.timedelta(days=1) for r in v[warm:]})
    return cuts


def origins_thin(rows: list[dict], deal: dict) -> list[dt.date]:
    """THIN_HISTORY (< 12 validated IPDs): pooled scoring on every IPD after the first two."""
    v = validated(rows)
    return sorted({dt.date.fromisoformat(r["payment_date"]) - dt.timedelta(days=1) for r in v[2:]})
