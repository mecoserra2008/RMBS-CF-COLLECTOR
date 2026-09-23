"""Candidate prepayment (paydown-rate) specifications. Each fits on an information set only (no I/O, no globals).

Observed quantity: the bond's annualised paydown rate per IPD, r_t = 1-(1-P_t/B_{t-1})^(12/m). Bond-only histories
cannot separate scheduled amortisation, prepayment and cured defaults, so r_t is the *total* paydown rate; the CDR and
recovery dimensions are therefore reference-only for bond-level ISINs (defaults are embedded in r_t) and are labelled
so in every output. ``fit`` returns ``None`` when the candidate is not identifiable on the information set.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Callable

from ..collateral import expit, logit


@dataclass(frozen=True)
class Obs:
    date: str
    available: str
    rate: float
    age: float           # months since first IPD (or first observation)
    coupon: float | None
    isin: str


@dataclass(frozen=True)
class CPRModel:
    name: str
    n_params: int
    fit: Callable[[list[Obs], dict], dict | None]
    path: Callable[[dict, int], float]


def _rates(obs):
    return [o.rate for o in obs]


def _last(obs, ctx):
    return {"level": obs[-1].rate} if obs else None


def _ma(k):
    def f(obs, ctx):
        if len(obs) < min(k, 3):
            return None
        return {"level": fmean(_rates(obs[-k:]))}
    return f


def _flat(p, k):
    return p["level"]


def _ewma(obs, ctx):
    if len(obs) < 4:
        return None
    best = None
    for hl in (1, 2, 4, 8):
        a = 1 - 0.5 ** (1 / hl)
        s, err = obs[0].rate, 0.0
        for o in obs[1:]:
            err += (o.rate - s) ** 2
            s = a * o.rate + (1 - a) * s
        if best is None or err < best[0] - 1e-15:
            best = (err, hl, s)
    return {"level": best[2], "half_life": best[1]}


def _ramp(obs, ctx):
    if len(obs) < 4:
        return None
    cur, lr = obs[-1].rate, fmean(_rates(obs))
    best = None
    for K in (4, 8, 12):      # slope fitted in-sample: ramp from each point toward the running mean
        err = 0.0
        for i in range(1, len(obs)):
            m = fmean(_rates(obs[:i]))
            pred = obs[i - 1].rate + (m - obs[i - 1].rate) / K
            err += (obs[i].rate - pred) ** 2
        if best is None or err < best[0] - 1e-15:
            best = (err, K)
    return {"level": cur, "long_run": lr, "K": best[1]}


def _ramp_path(p, k):
    w = min(k / p["K"], 1.0)
    return p["level"] + w * (p["long_run"] - p["level"])


def _season(obs, ctx):
    """Pooled seasoning curve: logit(rate) = a + b*log(1+age), across the universe (info set at the cut)."""
    pool = ctx.get("pooled_obs") or []
    if len(pool) < 8:
        return None
    xs = [math.log1p(o.age) for o in pool]
    ys = [logit(o.rate) for o in pool]
    mx, my = fmean(xs), fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    last = obs[-1] if obs else None
    if last is None:
        return None
    # deal-level intercept shift from its own residuals (shrinks nothing, keeps the pooled shape)
    shift = fmean([logit(o.rate) - (a + b * math.log1p(o.age)) for o in obs[-4:]])
    return {"a": a + shift, "b": b, "age0": last.age, "step_months": ctx.get("step_months", 3)}


def _season_path(p, k):
    return expit(p["a"] + p["b"] * math.log1p(p["age0"] + k * p["step_months"]))


def _rate(obs, ctx):
    """Refi incentive: rate = a + b*(coupon_t - coupon_mean) + c*age. Uses the bond coupon as the market-rate proxy
    only when the index path is filed; needs >= 8 observations with a coupon."""
    xs = [o for o in obs if o.coupon is not None]
    if len(xs) < 8:
        return None
    cm = fmean([o.coupon for o in xs])
    X = [(1.0, o.coupon - cm, o.age) for o in xs]
    y = [o.rate for o in xs]
    beta = _ols(X, y)
    if beta is None:
        return None
    last = xs[-1]
    return {"a": beta[0], "b": beta[1], "c": beta[2], "inc": last.coupon - cm, "age0": obs[-1].age,
            "step_months": ctx.get("step_months", 3)}


def _rate_path(p, k):
    return min(max(p["a"] + p["b"] * p["inc"] + p["c"] * (p["age0"] + k * p["step_months"]), 0.0), 0.99)


def _ar1(obs, ctx):
    if len(obs) < 6:
        return None
    x = [logit(o.rate) for o in obs]
    beta = _ols([(1.0, v) for v in x[:-1]], x[1:])
    if beta is None:
        return None
    c, phi = beta
    phi = max(min(phi, 0.99), -0.99)
    mu = c / (1 - phi)
    return {"mu": mu, "phi": phi, "x0": x[-1]}


def _ar1_path(p, k):
    return expit(p["mu"] + (p["phi"] ** k) * (p["x0"] - p["mu"]))


def _regime(obs, ctx):
    """2-state Markov switching on logit(rate), hard-assignment EM (fitted only with >= 24 observations)."""
    if len(obs) < 24:
        return None
    x = [logit(o.rate) for o in obs]
    lo, hi = min(x), max(x)
    if hi - lo < 1e-9:
        return None
    m = [lo, hi]
    for _ in range(50):
        s = [0 if abs(v - m[0]) <= abs(v - m[1]) else 1 for v in x]
        new = [fmean([v for v, t in zip(x, s) if t == j]) if any(t == j for t in s) else m[j] for j in (0, 1)]
        if new == m:
            break
        m = new
    s = [0 if abs(v - m[0]) <= abs(v - m[1]) else 1 for v in x]
    P = [[1.0, 1.0], [1.0, 1.0]]                    # Laplace-smoothed transition counts
    for a, b in zip(s, s[1:]):
        P[a][b] += 1
    P = [[r[0] / sum(r), r[1] / sum(r)] for r in P]
    return {"m0": m[0], "m1": m[1], "p00": P[0][0], "p11": P[1][1], "s0": s[-1]}


def _regime_path(p, k):
    pi = [1.0, 0.0] if p["s0"] == 0 else [0.0, 1.0]
    T = [[p["p00"], 1 - p["p00"]], [1 - p["p11"], p["p11"]]]
    for _ in range(k):
        pi = [pi[0] * T[0][0] + pi[1] * T[1][0], pi[0] * T[0][1] + pi[1] * T[1][1]]
    return pi[0] * expit(p["m0"]) + pi[1] * expit(p["m1"])


def _pooled(obs, ctx):
    """James-Stein shrinkage of the deal mean toward the universe mean (info set at the cut)."""
    pool = ctx.get("pooled_obs") or []
    other = [o.rate for o in pool]
    if not obs and len(other) < 3:
        return None
    if not obs:
        return {"level": fmean(other), "w": 0.0}
    own = _rates(obs[-12:])
    if len(other) < 3:
        return {"level": fmean(own), "w": 1.0}
    M = fmean(other)
    tau2 = pstdev(other) ** 2
    s2 = (pstdev(own) ** 2 if len(own) > 1 else tau2) / len(own)
    w = tau2 / (tau2 + s2) if tau2 + s2 > 0 else 0.5
    return {"level": w * fmean(own) + (1 - w) * M, "w": w}


def _ols(X, y):
    n, k = len(X), len(X[0])
    A = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    b = [sum(X[r][i] * y[r] for r in range(n)) for i in range(k)]
    # Gauss-Jordan with partial pivoting
    M = [row[:] + [bi] for row, bi in zip(A, b)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(k):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * bb for a, bb in zip(M[r], M[c])]
    return [M[i][k] / M[i][i] for i in range(k)]


CPR_MODELS: dict[str, CPRModel] = {m.name: m for m in [
    CPRModel("cpr_last", 1, _last, _flat),
    CPRModel("cpr_ma3", 1, _ma(3), _flat),
    CPRModel("cpr_ma6", 1, _ma(6), _flat),
    CPRModel("cpr_ma12", 1, _ma(12), _flat),
    CPRModel("cpr_ewma", 2, _ewma, _flat),
    CPRModel("cpr_ramp", 3, _ramp, _ramp_path),
    CPRModel("cpr_season", 3, _season, _season_path),
    CPRModel("cpr_rate", 3, _rate, _rate_path),
    CPRModel("cpr_ar1", 2, _ar1, _ar1_path),
    CPRModel("cpr_regime", 5, _regime, _regime_path),
    CPRModel("cpr_pooled", 1, _pooled, _flat),
]}

KEY_PARAM = {"cpr_ramp": "long_run", "cpr_season": "b", "cpr_rate": "b", "cpr_ar1": "mu", "cpr_regime": "m1"}


def key_param(name: str, params: dict) -> float:
    k = KEY_PARAM.get(name, "level")
    return float(params.get(k, float("nan")))


@dataclass(frozen=True)
class Candidate:
    cpr: str
    cdr: str
    recovery: str
    call: str

    @property
    def name(self) -> str:
        return f"{self.cpr}|{self.cdr}|{self.recovery}|{self.call}"

    @property
    def n_params(self) -> int:
        return CPR_MODELS[self.cpr].n_params + (0 if self.call == "call_never" else 0)


def grid(deal: dict, thin: bool, has_default_series: bool = False, cap: int = 200) -> list[Candidate]:
    cprs = ["cpr_pooled"] if thin else list(CPR_MODELS)
    cdrs = ["cdr_zero"] + (["cdr_last", "cdr_ma12"] if has_default_series else [])
    recs = ["rec_none"]
    calls = ["call_never"]
    if deal.get("clean_up_call_pct"):
        calls.append("call_at_threshold")
    if deal.get("call_issuer_stated"):
        calls.append("call_issuer_stated")
    out = [Candidate(a, b, c, d) for a in cprs for b in cdrs for c in recs for d in calls]
    return out[:cap]
