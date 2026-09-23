"""Projection engine: collateral -> waterfall -> bond cash flows; pricing (DM, WAL, spread duration, accrued).

Two state levels:
* bond level (every ISIN): the projected paydown rate is the bond's own annualised paydown rate (scheduled +
  prepayment + cured defaults). This is what issuer filings report for every deal.
* pool level (where the filing reports pool and subordinate balances): pool paydown is allocated to the class by the
  deal's amortisation mode (sequential / pro-rata) with per-note rounding -- used by the reproduction gate.
Never reads the network. Invariants (PIPELINE_SPEC s.5) are asserted on every projection.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Callable

from . import calendar as cal
from .collateral import period_rate
from .config import ipd_rules


class InvariantError(AssertionError):
    pass


def margin_at(deal: dict, d: dt.date) -> float | None:
    m = deal.get("margin_bp")
    if m is None:
        return None
    m = float(m)
    for s in deal.get("margin_steps") or []:
        if s.get("from") and dt.date.fromisoformat(str(s["from"])) <= d:
            m = float(s["margin_bp"])
    return m


def floor_at(deal: dict, d: dt.date) -> float | None:
    f = deal.get("coupon_floor")
    if f is None:
        return None
    a, b = deal.get("coupon_floor_from"), deal.get("coupon_floor_to")
    if a and d < dt.date.fromisoformat(str(a)):
        return None
    if b and d > dt.date.fromisoformat(str(b)):
        return None
    return float(f)


def coupon(deal: dict, d: dt.date, index: float) -> float | None:
    m = margin_at(deal, d)
    if m is None:
        return None
    c = index + m / 1e4
    f = floor_at(deal, d)
    return max(f, c) if f is not None else c


def allocate_principal(total: float, a_beg: float, sub_beg: float, mode: str, n_notes: int | None) -> float:
    """Class-A share of the principal available to the notes. Pro-rata by current balance; per-note amounts rounded
    to the cent (Magellan pays per note)."""
    if total <= 0 or a_beg <= 0:
        return 0.0
    share = 1.0 if mode == "sequential" else a_beg / (a_beg + max(sub_beg, 0.0))
    amt = min(total * share, a_beg)
    if n_notes:
        amt = round(round(amt / n_notes + 1e-9, 2) * n_notes, 2)
    return min(amt, a_beg)


@dataclass
class Assumptions:
    rate_path: Callable[[int], float]          # annual paydown rate for step k (1-based)
    index: float = 0.0                          # flat index (curve date recorded by the caller)
    call: str = "call_never"                    # call_never | call_at_threshold | call_issuer_stated
    call_date: dt.date | None = None
    call_factor: float | None = None            # bond factor that triggers the clean-up call (pool proxy)
    shortfall_ratio: float = 0.0                # fraction of principal due left unpaid (Hipocat-style)
    max_steps: int = 200


@dataclass
class Period:
    step: int
    date: dt.date
    beg: float
    due: float
    principal: float
    shortfall_carried: float
    interest: float
    end: float
    called: bool = False
    coupon: float | None = None


@dataclass
class Projection:
    periods: list[Period] = field(default_factory=list)

    @property
    def dates(self):
        return [p.date for p in self.periods]


def project(deal: dict, start: dt.date, balance: float, a: Assumptions, n: int | None = None) -> Projection:
    rules = ipd_rules(deal)
    if not rules:
        raise ValueError("CONFIG_INCOMPLETE: ipd_rule")
    orig = float(deal.get("original_balance") or 0) or None
    legal = dt.date.fromisoformat(str(deal["legal_final"])) if deal.get("legal_final") else None
    steps = n or a.max_steps
    dates = cal.next_ipds(rules, start, steps)
    out = Projection()
    bal, carried, prev = balance, 0.0, start
    for k, d in enumerate(dates, start=1):
        if bal <= 0.005:
            break
        months = max(1.0, round((d - prev).days / 30.4375))
        r = period_rate(a.rate_path(k), months)
        due = bal * r + carried
        paid = due * (1.0 - a.shortfall_ratio)
        called = False
        if a.call == "call_issuer_stated" and a.call_date and d >= a.call_date:
            called = True
        if a.call == "call_at_threshold" and a.call_factor and orig and (bal - paid) / orig <= a.call_factor:
            called = True
        if legal and d >= legal:
            called = True
        if called:
            paid = bal
        paid = min(paid, bal)
        new_carried = max(0.0, due - paid) if not called else 0.0
        cpn = coupon(deal, d, a.index)
        interest = bal * (cpn if cpn is not None else a.index) * (d - prev).days / 360.0
        out.periods.append(Period(k, d, bal, due, paid, new_carried, max(interest, 0.0), bal - paid, called, cpn))
        bal, carried, prev = bal - paid, new_carried, d
    check_invariants(out, balance)
    return out


def check_invariants(p: Projection, start_balance: float) -> None:
    last = start_balance
    for q in p.periods:
        if q.end < -1e-6 or q.principal < -1e-6 or q.interest < -1e-9:
            raise InvariantError(f"negative flow at step {q.step}")
        if q.end > last + 1e-6:
            raise InvariantError(f"balance increased at step {q.step}")
        if abs(q.beg - q.principal - q.end) > 1e-6:
            raise InvariantError(f"identity broken at step {q.step}")
        if not all(math.isfinite(x) for x in (q.beg, q.principal, q.interest, q.end)):
            raise InvariantError("non-finite cash flow")
        last = q.end
    paid = sum(q.principal for q in p.periods)
    rem = p.periods[-1].end if p.periods else start_balance
    if abs(paid + rem - start_balance) > 1e-4:
        raise InvariantError("principal not conserved")


# ------------------------------------------------------------------ pricing
def cashflows(p: Projection) -> list[tuple[dt.date, float, float, float]]:
    """(date, total, principal, interest)."""
    return [(q.date, q.principal + q.interest, q.principal, q.interest) for q in p.periods]


def pv(cfs, settle: dt.date, index: float, dm: float, first_reset: dt.date | None = None) -> float:
    """Floating-rate discounting: DF_k = prod 1/(1 + (index + DM) * tau_j), tau = ACT/360 between payment dates."""
    df, prev, tot = 1.0, settle, 0.0
    for d, amt, *_ in cfs:
        if d <= settle:
            continue
        df /= 1.0 + (index + dm) * (d - prev).days / 360.0
        tot += amt * df
        prev = d
    return tot


def solve_dm(cfs, settle: dt.date, index: float, dirty: float) -> float:
    """DM such that PV = dirty amount; bisection on [-5%, 50%] (monotone)."""
    lo, hi = -0.05, 0.5
    flo = pv(cfs, settle, index, lo) - dirty
    fhi = pv(cfs, settle, index, hi) - dirty
    if flo * fhi > 0:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = pv(cfs, settle, index, mid) - dirty
        if abs(fm) < 1e-8 * max(1.0, dirty) or hi - lo < 1e-12:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def wal(cfs, settle: dt.date) -> float:
    num = sum((d - settle).days / 365.0 * pr for d, _, pr, _ in cfs if d > settle)
    den = sum(pr for d, _, pr, _ in cfs if d > settle)
    return num / den if den > 0 else float("nan")


def accrued(balance: float, cpn: float, last_ipd: dt.date, settle: dt.date) -> float:
    return balance * cpn * (settle - last_ipd).days / 360.0


def spread_duration(cfs, settle: dt.date, index: float, dm: float, bump: float = 0.001) -> float:
    p0 = pv(cfs, settle, index, dm)
    return (pv(cfs, settle, index, dm - bump) - pv(cfs, settle, index, dm + bump)) / (2 * bump * p0) if p0 else float("nan")
