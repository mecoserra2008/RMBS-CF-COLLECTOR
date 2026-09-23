"""CFT-convention engine, vectorised over K configurations.

Monthly collateral step (the CFT ordering): defaults on the performing balance first (MDR), then scheduled principal
(level annuity on the surviving balance, remaining term WAM - t), then prepayments on what is left (SMM). Defaulted
balance is liquidated after `lag` months: recovery = (1 - severity) x defaulted, loss = severity x defaulted. With
servicer advancing, interest on defaulted-not-liquidated loans is advanced and reimbursed from the liquidation proceeds.

Quarterly (per-IPD) waterfall: fees -> class A interest -> subordinate interest -> excess spread cures the PDL ->
principal available (collections + recoveries + PDL cure) paid pro-rata (A share = A/(A+Sub)) or sequentially per
the trigger mode; clean-up call / call date / legal final redeem everything. Coupon = max(floor, index + margin(t)).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .. import calendar as cal
from ..config import ipd_rules
from ..engine import coupon as coupon_rate
from .conventions import default_mdr, prepay_smm


@dataclass
class State:
    start: dt.date              # last filed IPD
    pool: float                 # performing collateral balance (net of deemed losses)
    a: float                    # class A balance
    sub: float                  # subordinate notes balance
    pdl: float                  # principal deficiency ledger (>= 0)
    age0: float                 # loan age in months at start (proxy: months since closing/first IPD)
    index: float                # current index fixing (flat path; shifts are a grid dimension)
    prorata_filed: bool | None  # last filed pro-rata test (None = not filed)
    mode: str                   # 'pool' (filed collateral state) or 'proxy' (bond-level, repline inputs assumed)


@dataclass
class Grid:
    """Column arrays, one entry per configuration."""
    prepay_kind: np.ndarray
    prepay_level: np.ndarray
    default_kind: np.ndarray
    default_level: np.ndarray
    severity: np.ndarray
    lag: np.ndarray
    advancing: np.ndarray       # 0 none, 1 interest advanced
    call: np.ndarray            # 0 maturity, 1 clean-up call, 2 call date
    trigger: np.ndarray         # 0 as filed, 1 force sequential, 2 force pro-rata
    wac: np.ndarray
    wam: np.ndarray             # remaining term (months)
    index_shift: np.ndarray

    def __len__(self):
        return len(self.prepay_level)

    def take(self, idx):
        return Grid(**{k: getattr(self, k)[idx] for k in self.__dataclass_fields__})


def run(deal: dict, st: State, g: Grid, horizon_q: int | None = None, fee_bp: float = 0.0, sub_margin_bp: float | None = None):
    """Returns dict of arrays (K, Q): a_prin, a_int, a_end, pool_end, called; plus 'dates' (list of IPD dates)."""
    rules = ipd_rules(deal)
    legal = dt.date.fromisoformat(str(deal["legal_final"])) if deal.get("legal_final") else st.start + dt.timedelta(days=365 * 40)
    step_m = int(round(12 / len(rules[-1]["months"])))
    n_q = max(1, min(horizon_q or 10 ** 6, int((legal - st.start).days / 365.25 * 12 / step_m) + 1))
    dates = cal.next_ipds(rules, st.start, n_q)
    K = len(g)
    pool = np.full(K, st.pool)
    A = np.full(K, st.a)
    S = np.full(K, st.sub)
    pdl = np.full(K, st.pdl)
    lag = g.lag.astype(int)
    maxlag = int(lag.max()) if K else 0
    dq = np.zeros((K, maxlag + 1))          # ring buffer of defaulted balance awaiting liquidation
    adv_out = np.zeros(K)                   # outstanding advances
    alive = np.ones(K, dtype=bool)
    out = {k: np.zeros((K, n_q)) for k in ("a_prin", "a_int", "a_end", "pool_end", "called")}
    pac = float(deal.get("pool_at_closing") or 0) or None
    cu = float(deal.get("clean_up_call_pct") or 0)
    call_date = dt.date.fromisoformat(str(deal["call_issuer_stated"])) if deal.get("call_issuer_stated") else None
    sub_m = (sub_margin_bp if sub_margin_bp is not None else float(deal.get("sub_margin_bp") or deal.get("margin_bp") or 0)) / 1e4
    month, prev = 0, st.start
    rows = np.arange(K)
    for q, d in enumerate(dates):
        prin_c = np.zeros(K)
        int_c = np.zeros(K)
        loss = np.zeros(K)
        for _ in range(step_m):
            month += 1
            age = np.full(K, st.age0 + month)
            mdr = default_mdr(g.default_kind, g.default_level, age)
            smm = prepay_smm(g.prepay_kind, g.prepay_level, age)
            D = pool * mdr
            b1 = pool - D
            r = g.wac / 12.0
            n = np.maximum(g.wam - month, 1.0)
            pay = np.where(r > 0, b1 * r / (1 - (1 + r) ** (-n)), b1 / n)
            sched = np.clip(pay - b1 * r, 0.0, b1)
            pre = (b1 - sched) * smm
            int_c += pool * r * (1 - mdr)                     # interest on performing loans
            if maxlag:
                rs = (month - lag) % (maxlag + 1)
                liq = np.where(lag > 0, dq[rows, rs], D)
                dq[rows, rs] = np.where(lag > 0, 0.0, dq[rows, rs])
                dq[:, month % (maxlag + 1)] = np.where(lag > 0, D, 0.0)
                pending = dq.sum(axis=1)
            else:
                liq, pending = D, np.zeros(K)
            adv = g.advancing * pending * r
            int_c += adv
            adv_out += adv
            rec = liq * (1 - g.severity)
            reimb = np.minimum(adv_out, rec) * g.advancing
            adv_out -= reimb
            prin_c += sched + pre + rec - reimb
            loss += liq * g.severity
            pool = b1 - sched - pre
        days = (d - prev).days
        cpn = coupon_rate(deal, d, st.index) if deal.get("margin_bp") is not None else st.index
        cpnA = np.maximum(cpn + g.index_shift, float(deal["coupon_floor"])) if deal.get("coupon_floor") is not None else cpn + g.index_shift
        intA = A * cpnA * days / 360.0
        intS = S * (st.index + g.index_shift + sub_m) * days / 360.0
        fees = (pool + prin_c) * fee_bp / 1e4 * days / 360.0
        excess = int_c - fees - intA - intS
        pdl = pdl + loss
        cure = np.minimum(pdl, np.maximum(excess, 0.0))
        pdl -= cure
        avail = prin_c + cure
        pr = np.where(g.trigger == 1, False, np.where(g.trigger == 2, True, bool(st.prorata_filed)))
        share = np.where(pr & (A + S > 0), A / np.maximum(A + S, 1e-9), 1.0)
        pA = np.minimum(A, avail * share)
        pS = np.minimum(S, avail - pA)
        pA = np.minimum(A, pA + np.maximum(avail - pA - pS, 0.0))      # leftover (sub repaid) goes to A
        called = np.zeros(K, dtype=bool)
        if pac and cu:
            called |= (g.call == 1) & (pool <= cu * pac)
        if call_date:
            called |= (g.call == 2) & (d >= call_date)
        called |= d >= legal
        called &= alive & (A > 0)
        pA = np.where(called, A, pA)
        pS = np.where(called, S, pS)
        A = A - pA
        S = S - pS
        pool = np.where(called, 0.0, pool)
        out["a_prin"][:, q] = pA * alive
        out["a_int"][:, q] = intA * alive
        out["a_end"][:, q] = A
        out["pool_end"][:, q] = pool
        out["called"][:, q] = called
        alive &= A > 0.005
        prev = d
        if not alive.any():
            for k in out:
                out[k] = out[k][:, :q + 1]
            dates = dates[:q + 1]
            break
    out["dates"] = dates
    return out
