"""Row identities, chain continuity, IPD-gap detection (L6) and year-end reconciliation."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from . import calendar as cal
from .config import ipd_rules
from .history import fnum, is_kEUR

MATCH_DAYS = 10     # an observed IPD matches an expected one within +-10 days (printed dates vs rule; see calendar_checks)


def tol_balance(r: dict) -> float:
    return 1000.0 if is_kEUR(r) else 0.02


def row_checks(r: dict, deal: dict, valuation_date: dt.date) -> list[tuple[str, str, str]]:
    """Return [(check, result, detail)] with result PASS / FAIL / NA."""
    out = []
    g = lambda k: fnum(r, k)
    beg, prin, end = g("beg_balance"), g("principal_paid"), g("end_balance")
    if None not in (beg, prin, end):
        d = beg - prin - end
        out.append(("identity", "PASS" if abs(d) <= tol_balance(r) else "FAIL", f"{d:.2f}"))
    else:
        out.append(("identity", "NA", "beg/principal/end not all present"))
    orig, fac = g("original_balance"), g("pool_factor")
    if None not in (end, orig, fac) and orig:
        d = end / orig - fac
        out.append(("factor", "PASS" if abs(d) <= (1e-6 if not is_kEUR(r) else 1e-3) else "FAIL", f"{d:.2e}"))
    else:
        out.append(("factor", "NA", ""))
    n, pn = g("n_notes"), g("principal_per_note")
    if None not in (n, pn, prin):
        d = n * pn - prin
        out.append(("per_note", "PASS" if abs(d) <= 1.0 else "FAIL", f"{d:.2f}"))
    else:
        out.append(("per_note", "NA", ""))
    cpn, idx, m = g("coupon_rate"), g("index_rate"), g("margin_bp")
    floor = deal.get("coupon_floor")
    if cpn is not None and floor is not None and _floor_applies(deal, r["payment_date"]):
        out.append(("coupon_floor", "PASS" if cpn >= float(floor) - 1e-9 else "FAIL", f"{cpn:.5f}"))
    else:
        out.append(("coupon_floor", "NA", ""))
    if None not in (cpn, idx, m):
        expect = idx + m / 1e4
        if floor is not None and _floor_applies(deal, r["payment_date"]):
            expect = max(float(floor), expect)
        out.append(("coupon_formula", "PASS" if abs(cpn - expect) <= 1e-6 or abs(cpn - (idx + m / 1e4)) <= 1e-6
                    else "FAIL", f"{cpn - expect:.2e}"))
    else:
        out.append(("coupon_formula", "NA", ""))
    days, intr = g("accrual_days"), g("interest_paid")
    if None not in (beg, cpn, days, intr):
        exp = beg * cpn * days / 360
        ok = abs(exp - intr) <= 0.005 * abs(intr) + 1
        out.append(("interest_act360", "PASS" if ok else "FAIL", f"{exp - intr:.2f}"))
    else:
        out.append(("interest_act360", "NA", ""))
    if prin is not None:
        out.append(("principal_nonneg", "PASS" if prin >= -1e-9 else "FAIL", f"{prin}"))
    pd = dt.date.fromisoformat(r["payment_date"])
    out.append(("not_future", "PASS" if pd <= valuation_date else "FAIL", r["payment_date"]))
    due, short = g("principal_due"), g("principal_shortfall")
    if None not in (due, short, prin):
        d = due - prin - short
        out.append(("due_paid_shortfall", "PASS" if abs(d) <= tol_balance(r) else "FAIL", f"{d:.2f}"))
    return out


def _floor_applies(deal: dict, date: str) -> bool:
    f = deal.get("coupon_floor_from")
    return f is None or date >= str(f)


def expected_ipds(deal: dict, first: dt.date, last: dt.date) -> list[dt.date]:
    rules = ipd_rules(deal)
    if not rules:
        return []
    out = [first]
    cur = first
    while True:
        nxt = cal.next_ipds(rules, cur, 1)[0]
        if nxt > last:
            return out
        out.append(nxt)
        cur = nxt


def align(observed: list[str], expected: list[dt.date]) -> dict:
    """Map expected IPD -> observed payment_date (within MATCH_DAYS) or None."""
    obs = [dt.date.fromisoformat(d) for d in observed]
    res, used = {}, set()
    for e in expected:
        best = None
        for i, o in enumerate(obs):
            if i in used:
                continue
            if abs((o - e).days) <= MATCH_DAYS and (best is None or abs((o - e).days) < abs((obs[best] - e).days)):
                best = i
        if best is not None:
            used.add(best)
        res[e] = obs[best].isoformat() if best is not None else None
    return res


def chain_checks(rows: list[dict], expected: list[dt.date]) -> list[tuple[str, str, str, str]]:
    """end(t) = beg(t+1) for IPDs that are consecutive on the calendar. Returns (date, check, result, detail)."""
    out = []
    al = align([r["payment_date"] for r in rows], expected) if expected else {}
    consecutive = set()
    if al:
        seq = [al[e] for e in expected]
        for a, b in zip(seq, seq[1:]):
            if a and b:
                consecutive.add((a, b))
    for r0, r1 in zip(rows, rows[1:]):
        key = (r0["payment_date"], r1["payment_date"])
        e0, b1 = fnum(r0, "end_balance"), fnum(r1, "beg_balance")
        if not expected:
            out.append((r1["payment_date"], "chain", "NA", "no IPD calendar (CONFIG_INCOMPLETE)"))
            continue
        if key not in consecutive:
            out.append((r1["payment_date"], "chain", "GAP", f"not consecutive with {r0['payment_date']}"))
            continue
        if e0 is None or b1 is None:
            out.append((r1["payment_date"], "chain", "NA", "balance missing"))
            continue
        tol = max(tol_balance(r0), tol_balance(r1))
        d = e0 - b1
        out.append((r1["payment_date"], "chain", "PASS" if abs(d) <= tol else "BREAK", f"{d:.2f}"))
    return out


def gaps(isin: str, ticker: str, rows: list[dict], expected: list[dt.date]) -> list[dict]:
    al = align([r["payment_date"] for r in rows], expected)
    obs = sorted(r["payment_date"] for r in rows)
    out = []
    for e, o in al.items():
        if o is None:
            prev = max((x for x in obs if x < e.isoformat()), default="")
            nxt = min((x for x in obs if x > e.isoformat()), default="")
            out.append({"isin": isin, "ticker": ticker, "expected_ipd": e.isoformat(), "prev_observed": prev,
                        "next_observed": nxt, "status": "MISSING"})
    return out


def load_checkpoints(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as h:
        return list(csv.DictReader(h))


def yearend(isin: str, rows: list[dict], expected: list[dt.date], checkpoints: list[dict]) -> list[dict]:
    """Tie the chain to audited year-end balances. The chain value at date D is the end balance of the last IPD
    on/before D -- testable only if that IPD is the last *expected* IPD before D (no gap in between).
    Tolerance: kEUR rounding accumulates, so tol = max(1, 0.5 x rows chained since the last audited anchor) kEUR."""
    out = []
    obs = {r["payment_date"]: r for r in rows}
    al = align(list(obs), expected) if expected else {}
    for c in checkpoints:
        if c.get("isin_or_fund_senior") != isin:
            continue
        d = dt.date.fromisoformat(c["date"])
        scope = c.get("scope", "")
        rec = {"isin": isin, "date": c["date"], "checkpoint_kEUR": float(c["senior_notes_outstanding_kEUR"]),
               "source_url": c.get("source_url", ""), "chain_kEUR": "", "diff_kEUR": "", "tolerance_kEUR": ""}
        if scope and not scope.lower().startswith(("serie", "a2 only", "class a", "series a")):
            rec["result"] = f"NOT_COMPARABLE (checkpoint scope: {scope})"
            out.append(rec)
            continue
        last_exp = max((e for e in expected if e <= d), default=None)
        o = al.get(last_exp) if last_exp else None
        if not o or fnum(obs[o], "end_balance") is None:
            rec["result"] = "UNTESTABLE (last IPD before date not in chain)"
            out.append(rec)
            continue
        chain = fnum(obs[o], "end_balance") / 1e3
        k = sum(1 for r in rows if r["payment_date"] <= o and is_kEUR(r))
        tol = max(1.0, 0.5 * k) if is_kEUR(obs[o]) else 0.001
        diff = chain - rec["checkpoint_kEUR"]
        rec.update(chain_kEUR=round(chain, 3), diff_kEUR=round(diff, 3), tolerance_kEUR=tol,
                   result="TIED" if abs(diff) <= tol else "BREAK")
        out.append(rec)
    return out


def calendar_checks(isin: str, rows: list[dict], deal: dict) -> list[dict]:
    """Invariant 5: projected IPDs reproduce the observed calendar (last 12 observed)."""
    rules = ipd_rules(deal)
    out = []
    if not rules:
        return out
    for r in rows[-12:]:
        o = dt.date.fromisoformat(r["payment_date"])
        prev = cal.next_ipds(rules, o - dt.timedelta(days=MATCH_DAYS + 5), 1)[0]
        out.append({"isin": isin, "observed": o.isoformat(), "projected": prev.isoformat(),
                    "result": "EXACT" if prev == o else f"DEVIATION {(o - prev).days:+d}d"})
    return out
