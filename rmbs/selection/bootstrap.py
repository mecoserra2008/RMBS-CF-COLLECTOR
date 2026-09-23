"""Block bootstrap over logit-rate residuals (seeded, deterministic)."""
from __future__ import annotations

import random

from ..collateral import expit, logit


def residuals(rates: list[float], fitted: float) -> list[float]:
    return [logit(r) - logit(fitted) for r in rates]


def block_paths(res: list[float], n_steps: int, n_draws: int, seed: int, block: int = 4) -> list[list[float]]:
    """n_draws residual paths of length n_steps, built from contiguous blocks (circular)."""
    rng = random.Random(seed)
    if not res:
        return [[0.0] * n_steps for _ in range(n_draws)]
    L = len(res)
    out = []
    for _ in range(n_draws):
        p: list[float] = []
        while len(p) < n_steps:
            s = rng.randrange(L)
            p.extend(res[(s + j) % L] for j in range(min(block, L)))
        out.append(p[:n_steps])
    return out


def perturb(rate: float, e: float) -> float:
    return expit(logit(rate) + e)


def quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def crps(ens: list[float], y: float) -> float:
    """Empirical CRPS = E|X - y| - 0.5 E|X - X'| (sorted-sample O(n log n) form)."""
    n = len(ens)
    if n == 0:
        return float("nan")
    s = sorted(ens)
    t1 = sum(abs(x - y) for x in s) / n
    t2 = sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(s)) / (n * n)
    return t1 - t2
