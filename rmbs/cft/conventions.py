"""Market-standard speed conventions (the ones CFT exposes). All functions take loan age in months (>= 1)
and return monthly rates as numpy arrays.

PSA 100%: CPR rises 0.2% per month to 6% at month 30, flat after; x% PSA scales it.
SDA 100%: CDR 0.02% at month 1, +0.02%/month to 0.60% at month 30, flat to month 60, then -0.0095%/month to
0.03% at month 120, flat after; x% SDA scales it.
ABS: monthly prepayment as % of the ORIGINAL loan count: SMM_t = ABS / (1 - ABS * (t - 1)).
CPR/CDR are annual; SMM/MDR the monthly equivalents: 1 - (1 - annual)^(1/12).
"""
from __future__ import annotations

import numpy as np


def to_monthly(annual):
    return 1.0 - (1.0 - np.clip(annual, 0.0, 0.999999)) ** (1.0 / 12.0)


def psa_cpr(age, speed):
    return np.minimum(age, 30.0) / 30.0 * 0.06 * speed / 100.0


def sda_cdr(age, speed):
    a = np.asarray(age, dtype=float)
    base = np.where(a <= 30, 0.0002 * a,
                    np.where(a <= 60, 0.006, np.where(a <= 120, 0.006 - 0.000095 * (a - 60), 0.0003)))
    return np.maximum(base, 0.0) * speed / 100.0


def abs_smm(age, speed):
    a = np.asarray(age, dtype=float)
    den = 1.0 - speed * (a - 1.0)
    return np.where(den > speed, speed / np.maximum(den, 1e-9), 1.0)


PREPAY_KINDS = ("CPR", "SMM", "PSA", "ABS")
DEFAULT_KINDS = ("CDR", "MDR", "SDA")


def prepay_smm(kind: np.ndarray, level: np.ndarray, age: np.ndarray) -> np.ndarray:
    """kind codes: 0 CPR, 1 SMM, 2 PSA, 3 ABS; level in decimal (PSA/SDA in % of the standard curve)."""
    out = np.zeros_like(level, dtype=float)
    m = kind == 0
    out[m] = to_monthly(level[m])
    m = kind == 1
    out[m] = level[m]
    m = kind == 2
    out[m] = to_monthly(psa_cpr(age[m] if age.ndim else age, level[m]))
    m = kind == 3
    out[m] = abs_smm(age[m] if age.ndim else age, level[m])
    return np.clip(out, 0.0, 1.0)


def default_mdr(kind: np.ndarray, level: np.ndarray, age: np.ndarray) -> np.ndarray:
    """kind codes: 0 CDR, 1 MDR, 2 SDA."""
    out = np.zeros_like(level, dtype=float)
    m = kind == 0
    out[m] = to_monthly(level[m])
    m = kind == 1
    out[m] = level[m]
    m = kind == 2
    out[m] = to_monthly(sda_cdr(age[m] if age.ndim else age, level[m]))
    return np.clip(out, 0.0, 1.0)
