"""Scoring metrics (MODEL_SELECTION_PROTOCOL s.4)."""
from __future__ import annotations

import math
from statistics import fmean, pstdev


def nanmean(xs):
    v = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return fmean(v) if v else float("nan")


def rmse(errs):
    v = [e for e in errs if e is not None and not math.isnan(e)]
    return math.sqrt(fmean([e * e for e in v])) if v else float("nan")


def zscores(vals: list[float]) -> list[float]:
    ok = [v for v in vals if not math.isnan(v)]
    if len(ok) < 2:
        return [0.0] * len(vals)
    m, s = fmean(ok), pstdev(ok)
    return [0.0 if math.isnan(v) or s == 0 else (v - m) / s for v in vals]


def stab(xs: list[float]) -> float:
    v = [x for x in xs if not math.isnan(x)]
    if len(v) < 2:
        return float("nan")
    m = fmean(v)
    return pstdev(v) / abs(m) if m else float("nan")
