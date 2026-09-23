"""Reproduction gate G1-G6 (BLOOMBERG_CFT_METHODOLOGY s.4): replay the last min(12, N) validated IPDs with the
realised inputs printed in the filing and require the engine to reproduce the bond's cash flows.

Per-gate result: PASS | FAIL | INCOMPLETE (inputs not in the filings we hold) | N/A (item not reported by the deal).
gate_status: FAIL if any gate fails (BLOCKED_FOR_PRICING when G1-G3 fail); PASS only if G1-G4 pass and G5/G6 pass or
are N/A; otherwise INCOMPLETE. Nothing is inferred: a gate without inputs is INCOMPLETE, never PASS.
"""
from __future__ import annotations

import datetime as dt

from . import deals as D
from .engine import allocate_principal, coupon
from .history import fnum


def _agg(results: list[str], allow_na: bool) -> str:
    if not results:
        return "N/A" if allow_na else "INCOMPLETE"
    if "FAIL" in results:
        return "FAIL"
    if all(r == "PASS" for r in results):
        return "PASS"
    if allow_na and all(r in ("PASS", "N/A") for r in results):
        return "PASS" if "PASS" in results else "N/A"
    return "INCOMPLETE"


def replay(rows: list[dict], deal: dict, chain: dict[str, str]) -> dict:
    """``chain`` maps payment_date -> chain result (PASS/BREAK/GAP/NA) from validate.chain_checks."""
    rows = [r for r in rows if str(r.get("parse_status", "")).startswith(("ok", "DERIVED"))]
    win = rows[-12:]
    prev_next_index = {}                                  # index fixed at the previous consecutive IPD
    for a, b in zip(rows, rows[1:]):
        if chain.get(b["payment_date"]) == "PASS" and fnum(a, "next_index_rate") is not None:
            prev_next_index[b["payment_date"]] = fnum(a, "next_index_rate")
    g = {k: [] for k in ("G1", "G2", "G3", "G4", "G5", "G6")}
    notes = []
    for r in win:
        d = dt.date.fromisoformat(r["payment_date"])
        beg, prin, end = fnum(r, "beg_balance"), fnum(r, "principal_paid"), fnum(r, "end_balance")
        sb, se = fnum(r, "sub_beg_balance"), fnum(r, "sub_end_balance")
        mode = D.mode(deal, r)
        # G1 principal: allocate the realised principal available to the notes by the deal rule
        if None not in (beg, prin, end, sb, se) and mode:
            total = (beg + sb) - (end + se)
            n = int(fnum(r, "n_notes") or 0) or None
            hat = allocate_principal(total, beg, sb, mode, n)
            tol = max(0.5e-4 * beg, 1000.0)
            g["G1"].append("PASS" if abs(hat - prin) <= tol else "FAIL")
            if abs(hat - prin) > tol:
                notes.append(f"G1 {r['payment_date']}: engine {hat:.2f} vs filed {prin:.2f}")
        else:
            g["G1"].append("INCOMPLETE")
        # G2 interest from realised index + margin(t) + floor, ACT/360
        idx, days, intr = fnum(r, "index_rate"), fnum(r, "accrual_days"), fnum(r, "interest_paid")
        if deal.get("coupon_filed") is False:          # coupon/index in the row are derived from interest: circular
            idx = prev_next_index.get(r["payment_date"])
        cpn = coupon(deal, d, idx) if idx is not None else None
        if None not in (beg, cpn, days, intr):
            hat = beg * cpn * days / 360.0
            n = int(fnum(r, "n_notes") or 0)
            if deal.get("interest_rounding") == "per_note" and n:
                hat = round(beg / n * cpn * days / 360.0, 2) * n
            tol = max(0.02 * beg / 1e6, 0.02)
            ok = abs(hat - intr) <= tol
            g["G2"].append("PASS" if ok else "FAIL")
            if not ok:
                notes.append(f"G2 {r['payment_date']}: engine {hat:.2f} vs filed {intr:.2f} (cpn {cpn:.5f})")
        else:
            g["G2"].append("INCOMPLETE")
        # G3 ending-balance chain (the filing is the state)
        c = chain.get(r["payment_date"])
        if c == "PASS":
            g["G3"].append("PASS")
        elif c == "BREAK":
            g["G3"].append("FAIL")
            notes.append(f"G3 {r['payment_date']}: chain break")
        # G4 amortisation mode observed vs prescribed
        if None not in (beg, prin, sb, se) and mode:
            sub_p = sb - se
            tot = prin + sub_p
            if tot > 0:
                obs = "sequential" if sub_p <= 0.005 * tot else "pro_rata"
                g["G4"].append("PASS" if obs == mode else "FAIL")
                if obs != mode:
                    notes.append(f"G4 {r['payment_date']}: observed {obs}, rule says {mode}")
        else:
            g["G4"].append("INCOMPLETE")
        # G5 shortfall presence: needs available funds to predict; reported where principal_due is filed
        due = fnum(r, "principal_due")
        if due is not None:
            g["G5"].append("INCOMPLETE")
            notes.append(f"G5 {r['payment_date']}: shortfall reported (due {due:.0f}, paid {prin:.0f}); available funds not filed")
        # G6 reserve vs required
        res = fnum(r, "reserve_balance")
        req = (deal.get("reserve") or {}).get("required")
        if res is not None:
            g["G6"].append("PASS" if req and abs(res - float(req)) <= 0.01 * float(req) else "INCOMPLETE" if not req else "FAIL")
    out = {"n_replayed": len(win)}
    out["G1"] = _agg(g["G1"], False)
    out["G2"] = _agg(g["G2"], False)
    out["G3"] = _agg(g["G3"], False)
    out["G4"] = _agg(g["G4"], False)
    out["G5"] = _agg(g["G5"], True)
    out["G6"] = _agg(g["G6"], True)
    if not win:
        status = "NO_HISTORY"
    elif any(out[k] == "FAIL" for k in ("G1", "G2", "G3")):
        status = "BLOCKED_FOR_PRICING"
    elif any(out[k] == "FAIL" for k in ("G4", "G5", "G6")):
        status = "FAIL"
    elif all(out[k] == "PASS" for k in ("G1", "G2", "G3", "G4")) and all(out[k] in ("PASS", "N/A") for k in ("G5", "G6")):
        status = "PASS"
    else:
        status = "INCOMPLETE"
    missing = [k for k in ("G1", "G2", "G3", "G4", "G5", "G6") if out[k] == "INCOMPLETE"]
    detail = "; ".join(notes[:6])
    if missing:
        n1 = sum(1 for x in g["G1"] if x != "INCOMPLETE")
        detail = (f"inputs missing for {','.join(missing)} (G1 testable on {n1}/{len(win)} IPDs: needs pool/sub-class "
                  f"balances from the full report); " + detail).strip("; ")
    out["gate_status"] = status
    out["detail"] = detail
    return out
