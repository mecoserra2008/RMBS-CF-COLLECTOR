"""Collateral-rate conventions (Bloomberg-compatible metrics): CPR <-> SMM, CDR, annuity scheduled amortisation."""
from __future__ import annotations

import math


def smm(cpr: float) -> float:
    """Single monthly mortality from an annual CPR: SMM = 1 - (1 - CPR)^(1/12)."""
    return 1.0 - (1.0 - cpr) ** (1.0 / 12.0)


def cpr_from_smm(s: float) -> float:
    return 1.0 - (1.0 - s) ** 12


def period_rate(annual: float, months: float) -> float:
    """Fraction of balance lost over ``months`` at a constant annual rate (CPR or CDR)."""
    annual = min(max(annual, 0.0), 0.999999)
    return 1.0 - (1.0 - annual) ** (months / 12.0)


def annual_from_period(frac: float, months: float) -> float:
    frac = min(max(frac, 0.0), 0.999999)
    return 1.0 - (1.0 - frac) ** (12.0 / months)


def annuity_principal(balance: float, rate_per_period: float, n_periods: float) -> float:
    """Scheduled principal of a level-payment loan with ``n_periods`` remaining."""
    if balance <= 0 or n_periods <= 0:
        return max(balance, 0.0)
    if n_periods <= 1:
        return balance
    if abs(rate_per_period) < 1e-12:
        return balance / n_periods
    pay = balance * rate_per_period / (1.0 - (1.0 + rate_per_period) ** (-n_periods))
    return min(balance, pay - balance * rate_per_period)


def implied_remaining_term(balance: float, sched: float, rate_per_period: float) -> float:
    """Remaining periods implied by the last reported scheduled principal (same formula as the workbook G24)."""
    if sched <= 0 or balance <= 0:
        return float("inf")
    if abs(rate_per_period) < 1e-12:
        return balance / sched
    return math.log(1 + rate_per_period * balance / sched) / math.log(1 + rate_per_period)


def logit(p: float, eps: float = 1e-6) -> float:
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1 - p))


def expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
