import datetime as dt

import pytest

from helpers import DEAL, history
from rmbs import validate as V
from rmbs.schema import SCHEMA


def test_row_checks_pass_on_synthetic():
    rows = history(6)
    for r in rows:
        res = {c: x for c, x, _ in V.row_checks(r, DEAL, dt.date(2030, 1, 1))}
        assert res["identity"] == res["interest_act360"] == res["coupon_formula"] == res["not_future"] == "PASS"


def test_row_checks_failures():
    r = history(1)[0]
    r.update(end_balance=r["end_balance"] + 5, pool_factor=0.5, coupon_rate=-0.01, principal_per_note=1.0,
             principal_due=r["principal_paid"] + 10, principal_shortfall=0.0)
    res = {c: x for c, x, _ in V.row_checks(r, DEAL, dt.date(2000, 1, 1))}
    assert res["identity"] == res["factor"] == res["per_note"] == res["coupon_floor"] == "FAIL"
    assert res["not_future"] == res["due_paid_shortfall"] == res["interest_act360"] == "FAIL"
    empty = {c: "" for c in SCHEMA}
    empty.update(payment_date="2020-01-01")
    assert all(x in ("NA", "PASS") for _, x, _ in V.row_checks(empty, {}, dt.date(2030, 1, 1)))


def test_kEUR_tolerance():
    r = history(1)[0]
    r.update(source_section="Cuentas Anuales (kEUR)", end_balance=r["end_balance"] + 900, pool_factor="")
    res = {c: x for c, x, _ in V.row_checks(r, DEAL, dt.date(2030, 1, 1))}
    assert res["identity"] == "PASS"


def test_chain_gap_break_and_gaps():
    rows = history(8)
    exp = V.expected_ipds(DEAL, dt.date.fromisoformat(rows[0]["payment_date"]), dt.date.fromisoformat(rows[-1]["payment_date"]))
    assert len(exp) == 8
    assert all(res == "PASS" for _, _, res, _ in V.chain_checks(rows, exp))
    del rows[3]
    rows[5]["beg_balance"] += 10
    res = [x for _, _, x, _ in V.chain_checks(rows, exp)]
    assert "GAP" in res and "BREAK" in res
    g = V.gaps("XS_SYNTH", "S", rows, exp)
    assert len(g) == 1 and g[0]["status"] == "MISSING" and g[0]["prev_observed"] < g[0]["expected_ipd"]
    assert V.chain_checks(rows, [])[0][2] == "NA"
    assert V.expected_ipds({}, dt.date(2020, 1, 1), dt.date(2021, 1, 1)) == []


def test_yearend(tmp_path):
    rows = history(8)
    exp = V.expected_ipds(DEAL, dt.date.fromisoformat(rows[0]["payment_date"]), dt.date(2012, 1, 1))
    ye = [r for r in rows if r["payment_date"] < "2011-01-01"][-1]["end_balance"] / 1e3
    cp = lambda d, v, scope="Serie A", i="XS_SYNTH": {"isin_or_fund_senior": i, "date": d, "senior_notes_outstanding_kEUR": str(v), "scope": scope}
    rows2 = [r for k, r in enumerate(rows) if k not in (5, 6)]          # 2011-06 and 2011-09 missing
    cps = [cp("2010-12-31", ye), cp("2011-12-31", 1), cp("2009-12-31", rows[0]["beg_balance"] / 1e3),
           cp("2011-06-30", 1), cp("2010-12-31", 1, "sum of A classes"), cp("2010-12-31", 1, "", "OTHER")]
    out = V.yearend("XS_SYNTH", rows2, exp, cps)
    assert [o["result"][:12] for o in out] == ["TIED", "BREAK", "TIED", "UNTESTABLE (", "NOT_COMPARAB"]
    p = tmp_path / "c.csv"
    p.write_text("fund,isin_or_fund_senior,date,senior_notes_outstanding_kEUR,scope,source_url\nF,X,2020-12-31,1,Serie A,u\n")
    assert len(V.load_checkpoints(p)) == 1 and V.load_checkpoints(tmp_path / "none.csv") == []


def test_calendar_checks():
    rows = history(4)
    rows[1]["payment_date"] = (dt.date.fromisoformat(rows[1]["payment_date"]) + dt.timedelta(days=2)).isoformat()
    res = [c["result"] for c in V.calendar_checks("X", rows, DEAL)]
    assert res[0] == "EXACT" and res[1].startswith("DEVIATION +2d")
    assert V.calendar_checks("X", rows, {}) == []
