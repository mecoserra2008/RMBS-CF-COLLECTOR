"""Synthetic deal + history generator for engine/selection tests (quarterly, sequential, known paydown process)."""
import datetime as dt
import math
import random

from rmbs import calendar as cal
from rmbs.schema import SCHEMA

DEAL = {"isin": "XS_SYNTH", "deal": "Synthetic", "series": "A", "family": "bcp", "original_balance": 1_000_000_000.0,
        "notes": 10000, "denomination": 100000, "index": "EURIBOR_3M", "margin_bp": 20, "margin_steps": [],
        "coupon_floor": 0.0, "day_basis": "ACT/360",
        "ipd_rule": {"months": [3, 6, 9, 12], "day": 20, "convention": "following", "calendar": "TARGET"},
        "amortisation": {"mode": "pro_rata_if_test_pass"}, "triggers": {}, "reserve": {"required": 10_000_000},
        "clean_up_call_pct": 0.10, "legal_final": "2060-12-20", "swap": {}, "fees": [], "sources": [],
        "first_ipd": "2010-03-22", "pool_at_closing": 1_050_000_000.0, "publication_lag_days": 5}


def history(n=40, rate=0.10, noise=0.01, seed=7, sub_ratio=0.05, index=0.02, with_sub=True, start="2010-03-22"):
    rng = random.Random(seed)
    rules = [DEAL["ipd_rule"]]
    d0 = dt.date.fromisoformat(start)
    dates = [d0] + cal.next_ipds(rules, d0, n - 1)
    rows, bal, sub, prev = [], 900_000_000.0, 900_000_000.0 * sub_ratio, d0 - dt.timedelta(days=91)
    for d in dates:
        r = min(max(rate + rng.gauss(0, noise), 0.001), 0.5)
        q = 1 - (1 - r) ** 0.25
        tot = round((bal + sub) * q, 2)
        a = round(round(tot * bal / (bal + sub) / DEAL["notes"], 2) * DEAL["notes"], 2)
        sp = round(tot - a, 2)
        days = (d - prev).days
        cpn = index + 0.002
        row = {c: "" for c in SCHEMA}
        row.update(isin="XS_SYNTH", deal="Synthetic", tranche="A", report_month=d.isoformat()[:7], payment_date=d.isoformat(),
                   accrual_days=days, day_basis="Act/360", index_rate=index, margin_bp=20, coupon_rate=cpn,
                   original_balance=1e9, beg_balance=bal, principal_paid=a, end_balance=round(bal - a, 2),
                   pool_factor=round(bal - a, 2) / 1e9, interest_paid=round(bal * cpn * days / 360, 2), n_notes=10000,
                   prorata_test="PASS", reserve_balance=10_000_000.0, coll_end_balance_net=(bal - a + sub - sp) * 1.01,
                   source_url="synthetic", source_section="synthetic", parse_status="ok: synthetic")
        if with_sub:
            row.update(sub_beg_balance=sub, sub_end_balance=round(sub - sp, 2))
        rows.append(row)
        bal, sub, prev = round(bal - a, 2), round(sub - sp, 2), d
    return rows
