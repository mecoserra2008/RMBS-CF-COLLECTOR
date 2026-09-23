import datetime as dt

import pytest
from hypothesis import given, settings, strategies as st

from helpers import DEAL
from rmbs import collateral as K
from rmbs import engine as E

S0 = dt.date(2026, 3, 20)


def proj(rate=0.1, bal=500e6, **kw):
    return E.project(DEAL, S0, bal, E.Assumptions(rate_path=lambda k: rate, index=0.02, **kw))


def test_collateral_conversions():
    assert K.cpr_from_smm(K.smm(0.12)) == pytest.approx(0.12)
    assert K.annual_from_period(K.period_rate(0.2, 3), 3) == pytest.approx(0.2)
    assert K.annuity_principal(100, 0.0, 4) == 25 and K.annuity_principal(100, 0.01, 1) == 100
    assert K.annuity_principal(0, 0.01, 10) == 0
    p = K.annuity_principal(1000, 0.01, 10)
    assert K.implied_remaining_term(1000, p, 0.01) == pytest.approx(10)
    assert K.implied_remaining_term(1000, 100, 0.0) == 10 and K.implied_remaining_term(0, 1, 0.1) == float("inf")
    assert K.expit(K.logit(0.3)) == pytest.approx(0.3)


def test_zero_cpr_amortises_by_legal_final():
    p = proj(0.0)
    assert p.periods[-1].date == dt.date(2060, 12, 20) and p.periods[-1].called and p.periods[-1].end == 0


def test_call_modes_and_shortfall():
    p = proj(0.1, call="call_issuer_stated", call_date=dt.date(2027, 12, 20))
    assert p.periods[-1].date == dt.date(2027, 12, 20) and p.periods[-1].end == 0
    p = proj(0.1, call="call_at_threshold", call_factor=0.3)
    assert p.periods[-1].end == 0 and p.periods[-2].end / 1e9 > 0.3
    p = proj(0.2, shortfall_ratio=0.5)
    assert p.periods[0].principal == pytest.approx(p.periods[0].due * 0.5)
    assert p.periods[1].due > p.periods[1].beg * K.period_rate(0.2, 3)            # carried forward
    with pytest.raises(ValueError):
        E.project({"ipd_rule": None}, S0, 1.0, E.Assumptions(rate_path=lambda k: 0.1))


def test_coupon_margin_steps_and_floor():
    d = dict(DEAL, margin_steps=[{"from": "2030-01-01", "margin_bp": 40}], coupon_floor_from="2027-01-01", coupon_floor_to="2028-01-01")
    assert E.coupon(d, dt.date(2026, 1, 1), -0.01) == pytest.approx(-0.008)        # before floor window
    assert E.coupon(d, dt.date(2027, 6, 1), -0.01) == 0.0
    assert E.coupon(d, dt.date(2029, 1, 1), -0.01) == pytest.approx(-0.008)        # after floor window
    assert E.coupon(d, dt.date(2031, 1, 1), 0.02) == pytest.approx(0.024)
    assert E.coupon({}, dt.date(2031, 1, 1), 0.02) is None


def test_allocate_principal_reproduces_magellan3_aug2026():
    # realised principal to all notes, pro-rata by balance, per-note rounding -> filed 3,753,506.25
    a_beg, sub_beg = 114598575.00, 3441892.50 + 1606216.50 + 3747838.50
    total = (a_beg + sub_beg) - (110845068.75 + 3329167.50 + 1553611.50 + 3625093.50)
    assert E.allocate_principal(total, a_beg, sub_beg, "pro_rata", 141375) == pytest.approx(3753506.25)
    assert E.allocate_principal(total, a_beg, sub_beg, "sequential", None) == pytest.approx(total)
    assert E.allocate_principal(0, a_beg, sub_beg, "pro_rata", 1) == 0


def test_pricing_roundtrip_wal_accrued():
    p = proj(0.1)
    cf = E.cashflows(p)
    for dm in (0.001, 0.005, 0.02):
        price = E.pv(cf, S0, 0.02, dm)
        assert E.solve_dm(cf, S0, 0.02, price) == pytest.approx(dm, abs=1e-9)
    assert E.pv(cf, S0, 0.02, 0.0) == pytest.approx(E.pv(cf, S0, 0.0, 0.02))
    assert E.solve_dm(cf, S0, 0.02, 1e15) != E.solve_dm(cf, S0, 0.02, 1e15)       # nan when unbracketed
    # flat amortiser: equal principal each quarter -> WAL = mean payment time
    flat = [(S0 + dt.timedelta(days=91 * k), 25.0, 25.0, 0.0) for k in range(1, 5)]
    assert E.wal(flat, S0) == pytest.approx(sum(91 * k for k in range(1, 5)) / 4 / 365)
    assert E.accrued(1e6, 0.0365, S0, S0 + dt.timedelta(days=36)) == pytest.approx(3650.0)
    assert E.spread_duration(cf, S0, 0.02, 0.005) > 0
    assert E.wal([], S0) != E.wal([], S0)


def test_invariant_violations_detected():
    p = proj(0.1)
    p.periods[1].end = p.periods[0].end + 1
    with pytest.raises(E.InvariantError):
        E.check_invariants(p, 500e6)
    p = proj(0.1)
    p.periods[0].interest = -1
    with pytest.raises(E.InvariantError):
        E.check_invariants(p, 500e6)
    p = proj(0.1)
    p.periods[0].principal += 1
    with pytest.raises(E.InvariantError):
        E.check_invariants(p, 500e6)


@settings(max_examples=60, deadline=None)
@given(cpr=st.floats(0.0, 0.6), bal=st.floats(1e3, 2e9), sr=st.floats(0.0, 0.9))
def test_property_balances_nonnegative_monotone_finite(cpr, bal, sr):
    p = E.project(DEAL, S0, bal, E.Assumptions(rate_path=lambda k: cpr, index=0.02, shortfall_ratio=sr))
    last = bal
    for q in p.periods:
        assert 0 <= q.end <= last + 1e-6 and q.interest >= 0
        last = q.end
    assert last <= 0.005


@settings(max_examples=40, deadline=None)
@given(c1=st.floats(0.01, 0.5), dc=st.floats(0.01, 0.3))
def test_property_higher_cpr_shorter_wal(c1, dc):
    w1 = E.wal(E.cashflows(proj(c1)), S0)
    w2 = E.wal(E.cashflows(proj(c1 + dc)), S0)
    assert w2 <= w1 + 1e-9
