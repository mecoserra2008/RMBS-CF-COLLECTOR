"""Smoothness of a projected cash-flow path, measured as continuation of the filed history.

The object is the class-A paydown rate per IPD, rho_t = principal_t / balance_{t-1}. From the last N filed IPDs a
linear trend rho = a + b * t (t in years) is fitted; sigma = residual s.d. (floored).

  seam_level = (rho_1_hat - trend(t_1)) / sigma          jump between the filed path and the first projected IPD
  seam_slope = (slope of rho_hat over year 1 - b) / sigma   kink in the trend at the seam
  roughness  = RMS(second differences of rho_hat) / sigma   wiggles after the seam (IPDs before the terminal redemption)
  terminal   = size of the final balloon relative to sigma (clean-up call / legal final); weight 0 by default because a
               contractual redemption is not 'noise'

  S = w1 * seam_level^2 + w2 * seam_slope^2 + w3 * roughness^2 + w4 * terminal^2         (lower = smoother)

Why relative to history: in isolation every constant-CPR projection is perfectly smooth (and CPR = 0 is the smoothest
of all), so smoothness alone cannot rank configurations. Continuity with the filings is what makes it informative.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from ..history import fnum


def history_trend(rows: list[dict], n: int = 8):
    pts = [(dt.date.fromisoformat(r["payment_date"]), fnum(r, "principal_paid") / fnum(r, "beg_balance"))
           for r in rows if fnum(r, "beg_balance") and fnum(r, "principal_paid") is not None][-n:]
    if len(pts) < 2:
        return None
    t0 = pts[-1][0]
    t = np.array([(d - t0).days / 365.25 for d, _ in pts])
    y = np.array([v for _, v in pts])
    if len(pts) >= 3 and np.ptp(t) > 0:
        b, a = np.polyfit(t, y, 1)
        res = y - (a + b * t)
        sd = float(np.sqrt(np.sum(res ** 2) / max(len(y) - 2, 1)))
    else:
        a, b, sd = float(y[-1]), 0.0, float(np.std(y))
    sigma = max(sd, 0.1 * float(np.mean(np.abs(y))), 1e-4)
    return {"a": float(a), "b": float(b), "sigma": sigma, "t0": t0, "n": len(pts), "last": float(y[-1])}


def score(proj: dict, a0: float, trend: dict, weights: dict) -> dict:
    """Vectorised over configurations. proj: engine output (arrays K x Q)."""
    prin, aend = proj["a_prin"], proj["a_end"]
    K, Q = prin.shape
    beg = np.concatenate([np.full((K, 1), a0), aend[:, :-1]], axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rho = np.where(beg > 0.005, prin / beg, np.nan)
    called = proj["called"].astype(bool)
    t = np.array([(d - trend["t0"]).days / 365.25 for d in proj["dates"]])
    sig = trend["sigma"]
    pre_term = ~called & (np.nan_to_num(rho) < 0.999)
    r1 = rho[:, 0]
    level = (r1 - (trend["a"] + trend["b"] * t[0])) / sig
    k4 = min(4, Q)
    if k4 >= 2:
        slope = ((rho[:, k4 - 1] - rho[:, 0]) / max(t[k4 - 1] - t[0], 1e-9) - trend["b"]) / sig
    else:
        slope = np.zeros(K)
    slope = np.nan_to_num(slope, nan=0.0)
    rr = np.where(pre_term, rho, np.nan)
    d2 = rr[:, 2:] - 2 * rr[:, 1:-1] + rr[:, :-2] if Q >= 3 else np.zeros((K, 0))
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore", RuntimeWarning)
        rough = np.sqrt(np.nanmean(d2 ** 2, axis=1)) / sig if d2.shape[1] else np.zeros(K)
    rough = np.nan_to_num(rough, nan=0.0)
    term_idx = np.argmax(called | (np.nan_to_num(rho) >= 0.999), axis=1)
    has_term = (called | (np.nan_to_num(rho) >= 0.999)).any(axis=1)
    prev_idx = np.maximum(term_idx - 1, 0)
    rows = np.arange(K)
    term = np.where(has_term, (1.0 - np.nan_to_num(rho[rows, prev_idx])) / sig, 0.0)
    w = weights
    S = (w.get("seam_level", 1) * level ** 2 + w.get("seam_slope", 1) * slope ** 2 + w.get("roughness", 1) * rough ** 2
         + w.get("terminal", 0) * term ** 2)
    return {"S": S, "seam_level": level, "seam_slope": slope, "roughness": rough, "terminal": term,
            "rho1": r1, "rho1_annual": 1 - (1 - np.clip(r1, 0, 1)) ** 4}


def filed_rates(rows: list[dict]) -> dict:
    """Collateral facts printed in the last filing: prepayment CPR (12m if printed, else the period CPR) and the
    default rate implied by deemed losses (annualised). Missing -> not used."""
    last = rows[-1]
    out = {}
    cpr = fnum(last, "coll_cpr_12m")
    cpr = cpr if cpr is not None else fnum(last, "coll_cpr_reported")
    if cpr is not None:
        out["cpr"] = cpr
    dl, cb = fnum(last, "coll_deemed_losses"), fnum(last, "coll_beg_balance")
    if dl is not None and cb:
        out["cdr"] = 1 - (1 - dl / cb) ** 4
    t = str(last.get("prorata_test", "")).upper()
    if t.startswith(("PASS", "FAIL")):
        out["prorata"] = t.startswith("PASS")
    return out


def consistency(g, age0: float, filed: dict, weights: dict) -> dict:
    """Penalty for configurations that contradict what the last filing prints (the seam in collateral space):
    z_cpr = (CPR_cfg - CPR_filed) / max(1%, 20% x CPR_filed); z_cdr = (CDR_cfg - CDR_filed) / max(0.25%, 50% x CDR_filed);
    trigger = 1 when a forced amortisation mode contradicts the filed pro-rata test. C = sum of w x z^2."""
    from .conventions import default_mdr, prepay_smm
    age = np.full(len(g), age0 + 1.0)
    cpr = 1 - (1 - prepay_smm(g.prepay_kind, g.prepay_level, age)) ** 12
    cdr = 1 - (1 - default_mdr(g.default_kind, g.default_level, age)) ** 12
    K = len(g)
    zc = (cpr - filed["cpr"]) / max(0.01, 0.2 * filed["cpr"]) if "cpr" in filed else np.zeros(K)
    zd = (cdr - filed["cdr"]) / max(0.0025, 0.5 * filed["cdr"]) if "cdr" in filed else np.zeros(K)
    if "prorata" in filed:
        tr = np.where(g.trigger == 1, not filed["prorata"], np.where(g.trigger == 2, filed["prorata"], True))
        zt = (~tr).astype(float)
    else:
        zt = np.zeros(K)
    C = weights.get("cpr", 1) * zc ** 2 + weights.get("cdr", 1) * zd ** 2 + weights.get("trigger", 1) * 4 * zt
    return {"C": C, "z_cpr": zc, "z_cdr": zd, "trigger_contradiction": zt, "cpr_cfg": cpr, "cdr_cfg": cdr,
            "identified": sorted(filed)}
