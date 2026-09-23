"""Build the configuration grid and the starting state for an ISIN."""
from __future__ import annotations

import datetime as dt
import itertools

import numpy as np

from ..history import fnum
from .engine import Grid, State

PK = {"CPR": 0, "SMM": 1, "PSA": 2, "ABS": 3}
DK = {"CDR": 0, "MDR": 1, "SDA": 2}
CALL = {"maturity": 0, "clean_up": 1, "call_date": 2}
TRIG = {"as_filed": 0, "force_sequential": 1, "force_pro_rata": 2}
PK_INV = {v: k for k, v in PK.items()}
DK_INV = {v: k for k, v in DK.items()}
CALL_INV = {v: k for k, v in CALL.items()}
TRIG_INV = {v: k for k, v in TRIG.items()}


def state_for(rows: list[dict], deal: dict, upto: int | None = None) -> State | None:
    """Starting state from the last filed IPD (rows[:upto]). 'pool' mode needs the filed pool, WAC and sub balances;
    otherwise 'proxy' mode: pool = class A balance (sequential, no subordinate), repline WAC/WAM searched on the grid."""
    rows = [r for r in rows[:upto] if fnum(r, "end_balance") is not None]
    if not rows:
        return None
    last = rows[-1]
    d = dt.date.fromisoformat(last["payment_date"])
    first = dt.date.fromisoformat(str(deal.get("first_ipd") or deal.get("closing_date") or rows[0]["payment_date"]))
    age0 = max(1.0, (d - first).days / 30.4375 + 3)
    pool = fnum(last, "coll_end_balance_net") or fnum(last, "coll_end_balance")
    sub = fnum(last, "sub_end_balance")
    idx = fnum(last, "next_index_rate")
    if idx is None:
        idx = fnum(last, "index_rate")
    if idx is None and fnum(last, "coupon_rate") is not None and deal.get("margin_bp") is not None:
        idx = fnum(last, "coupon_rate") - float(deal["margin_bp"]) / 1e4
    t = str(last.get("prorata_test", "")).upper()
    pr = True if t.startswith("PASS") else False if t.startswith("FAIL") else None
    a = fnum(last, "end_balance")
    if pool and fnum(last, "wac") and sub is not None:
        return State(d, pool, a, sub, max(0.0, a + sub - pool), age0, idx or 0.0, pr, "pool")
    if pool and fnum(last, "wac"):          # pool + WAC filed, subordinate balance not: sub = pool - A (no PDL)
        return State(d, pool, a, max(pool - a, 0.0), 0.0, age0, idx or 0.0, pr, "pool_sub_implied")
    return State(d, a, a, 0.0, 0.0, age0, idx or 0.0, pr, "proxy")


def implied_wam(rows: list[dict]) -> float | None:
    last = rows[-1]
    pool, sched, wac = fnum(last, "coll_end_balance_net") or fnum(last, "coll_end_balance"), fnum(last, "coll_sched_principal"), fnum(last, "wac")
    if not (pool and sched and wac):
        return None
    r = wac / 12
    ms = sched / 3.0                        # quarterly report -> monthly scheduled principal
    return float(np.log(1 + r * pool / ms) / np.log(1 + r)) if ms > 0 else None


def build(cfg: dict, deal: dict, st: State, rows: list[dict]) -> Grid:
    pre = [(PK[k], v) for k, vs in cfg["prepay"].items() for v in (vs or [])]
    dfl = [(0, 0.0)] + [(DK[k], v) for k, vs in cfg["default"].items() for v in (vs or []) if v > 0]
    losscombos = []
    for dk, dv in dfl:
        if dv == 0:
            losscombos.append((dk, dv, 0.0, 0, 0))
        else:
            for sev, lag, adv in itertools.product(cfg["severity"], cfg["recovery_lag_months"], cfg["advancing"]):
                losscombos.append((dk, dv, sev, lag, adv))
    calls = [CALL["maturity"]]
    if "clean_up" in cfg["call"] and deal.get("clean_up_call_pct") and deal.get("pool_at_closing"):
        calls.append(CALL["clean_up"])
    if "call_date" in cfg["call"] and deal.get("call_issuer_stated"):
        calls.append(CALL["call_date"])
    am = (deal.get("amortisation") or {}).get("mode", "")
    can_pr = st.sub > 0 and am != "sequential"
    trigs = [TRIG[t] for t in cfg["trigger"] if can_pr or t == "as_filed"]
    if st.mode == "proxy":
        repl = list(itertools.product(cfg["proxy_wac"], cfg["proxy_wam_months"]))
    else:
        w = implied_wam(rows) or 240.0
        repl = [(fnum(rows[-1], "wac"), w)]
    combos = list(itertools.product(pre, losscombos, calls, trigs, repl, cfg["index_shift"]))
    cols = {k: [] for k in Grid.__dataclass_fields__}
    for (pk, pv), (dk, dv, sev, lag, adv), c, t, (wac, wam), sh in combos:
        for k, v in (("prepay_kind", pk), ("prepay_level", pv), ("default_kind", dk), ("default_level", dv),
                     ("severity", sev), ("lag", lag), ("advancing", adv), ("call", c), ("trigger", t),
                     ("wac", wac), ("wam", wam), ("index_shift", sh)):
            cols[k].append(v)
    return Grid(**{k: np.asarray(v, dtype=float) for k, v in cols.items()})


def describe(g: Grid, i: int) -> dict:
    dk = int(g.default_kind[i])
    return {"prepay": f"{PK_INV[int(g.prepay_kind[i])]} {g.prepay_level[i]:g}",
            "default": "none" if g.default_level[i] == 0 else f"{DK_INV[dk]} {g.default_level[i]:g}",
            "severity": g.severity[i], "lag_m": int(g.lag[i]), "advancing": int(g.advancing[i]),
            "call": CALL_INV[int(g.call[i])], "trigger": TRIG_INV[int(g.trigger[i])],
            "wac": round(float(g.wac[i]), 6), "wam_m": round(float(g.wam[i]), 1), "index_shift": g.index_shift[i]}
