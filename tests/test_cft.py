"""CFT conventions, vectorised engine, grid, smoothness score, filing consistency, criterion check, BBG replication."""
import datetime as dt

import numpy as np
import pytest
import yaml

from conftest import ROOT
from helpers import DEAL, history
from rmbs.cft import conventions as V
from rmbs.cft import engine as E
from rmbs.cft.bbg_replicate import replicate
from rmbs.cft.criterion import check, spearman
from rmbs.cft.grid import build, describe, implied_wam, state_for
from rmbs.cft.run import rank_isin
from rmbs.cft.smooth import consistency, filed_rates, history_trend, score

CFG = yaml.safe_load((ROOT / "config" / "cft.yaml").read_text())
SMALL = dict(CFG, prepay={"CPR": [0.0, 0.06, 0.1, 0.14], "PSA": [100], "ABS": [0.01]}, default={"CDR": [0.0, 0.01], "SDA": [100]},
             severity=[0.35], recovery_lag_months=[0, 12], advancing=[0, 1], proxy_wac=[0.03], proxy_wam_months=[240],
             horizon_quarters=12, top_n_output=5)


def test_conventions():
    assert V.psa_cpr(np.array([15.0, 30.0, 60.0]), 100) == pytest.approx([0.03, 0.06, 0.06])
    assert V.psa_cpr(np.array([30.0]), 200) == pytest.approx([0.12])
    assert V.sda_cdr(np.array([1.0, 30.0, 45.0, 120.0, 200.0]), 100) == pytest.approx([0.0002, 0.006, 0.006, 0.0003, 0.0003])
    assert V.to_monthly(np.array([0.12]))[0] == pytest.approx(1 - 0.88 ** (1 / 12))
    assert V.abs_smm(np.array([1.0, 11.0]), 0.01) == pytest.approx([0.01, 0.01 / 0.9])
    k = np.array([0, 1, 2, 3])
    lv = np.array([0.12, 0.01, 100.0, 0.01])
    out = V.prepay_smm(k, lv, np.full(4, 40.0))
    assert out[0] == pytest.approx(V.to_monthly(np.array([0.12]))[0]) and out[1] == 0.01
    assert out[2] == pytest.approx(V.to_monthly(np.array([0.06]))[0])   # 100 PSA seasoned = 6% CPR
    assert V.prepay_smm(np.array([2]), np.array([200.0]), np.array([40.0]))[0] == pytest.approx(V.to_monthly(np.array([0.12]))[0])
    d = V.default_mdr(np.array([0, 1, 2]), np.array([0.02, 0.001, 100.0]), np.full(3, 45.0))
    assert d[1] == 0.001 and d[2] == pytest.approx(V.to_monthly(np.array([0.006]))[0])


def state(**kw):
    s = dict(start=dt.date(2026, 3, 20), pool=110e6, a=100e6, sub=8e6, pdl=0.0, age0=60, index=0.02, prorata_filed=True, mode="pool")
    s.update(kw)
    return E.State(**s)


def grid1(**kw):
    base = dict(prepay_kind=0, prepay_level=0.1, default_kind=0, default_level=0.0, severity=0.35, lag=12, advancing=0,
                call=0, trigger=0, wac=0.03, wam=240.0, index_shift=0.0)
    base.update(kw)
    return E.Grid(**{k: np.array([v], dtype=float) for k, v in base.items()})


def test_engine_conservation_and_modes():
    p = E.run(DEAL, state(), grid1())
    assert p["a_prin"].sum() == pytest.approx(100e6, rel=1e-9) and p["a_end"][0, -1] == 0
    assert np.all(np.diff(p["a_end"][0]) <= 1e-6)
    seq = E.run(DEAL, state(), grid1(trigger=1), horizon_q=4)
    pro = E.run(DEAL, state(), grid1(trigger=2), horizon_q=4)
    assert seq["a_prin"][0, 0] > pro["a_prin"][0, 0]
    faster = E.run(DEAL, state(), grid1(prepay_level=0.2), horizon_q=4)
    assert faster["a_prin"][0, 0] > pro["a_prin"][0, 0]
    called = E.run(DEAL, state(pool=90e6), grid1(call=1))                # pool 90m <= 10% x 1.05bn -> call at first IPD
    assert called["called"][0, 0] == 1 and called["a_prin"][0, 0] == pytest.approx(100e6)
    d = dict(DEAL, call_issuer_stated="2027-06-20")
    cd = E.run(d, state(), grid1(call=2))
    assert cd["dates"][-1] == dt.date(2027, 6, 21)
    loss = E.run(DEAL, state(), grid1(default_level=0.05, lag=6, advancing=1, severity=0.5), horizon_q=8)
    nol = E.run(DEAL, state(), grid1(default_level=0.05, lag=0, advancing=0, severity=0.5), horizon_q=8)
    assert np.all(np.isfinite(loss["a_prin"])) and loss["a_int"][0, 0] == pytest.approx(nol["a_int"][0, 0])
    k = E.run(DEAL, state(), E.Grid(**{f: np.concatenate([getattr(grid1(), f), getattr(grid1(prepay_level=0.2), f)]) for f in E.Grid.__dataclass_fields__}), horizon_q=3)
    assert k["a_prin"].shape == (2, 3)


def test_grid_state_smoothness_consistency():
    rows = history(20, rate=0.10, noise=0.003)
    for r in rows:
        r.update(coll_end_balance_net=r["end_balance"] + r["sub_end_balance"], wac=0.03, coll_sched_principal=1e6,
                 coll_cpr_12m=0.08, coll_deemed_losses=1000.0, coll_beg_balance=r["beg_balance"] * 1.05)
    st = state_for(rows, DEAL)
    assert st.mode == "pool" and st.prorata_filed is True and implied_wam(rows) > 12
    g = build(SMALL, DEAL, st, rows)
    assert len(g) > 50 and describe(g, 0)["prepay"] == "CPR 0"
    tr = history_trend(rows)
    pr = E.run(DEAL, st, g, horizon_q=12)
    sc = score(pr, st.a, tr, SMALL["smoothness"]["weights"])
    best = int(np.argmin(sc["S"]))
    assert abs(sc["rho1_annual"][best] - 0.10) < 0.04                   # smoothest continues the filed ~10% paydown
    assert sc["S"][int(np.argmin(g.prepay_level))] > sc["S"][best]      # CPR 0 is NOT the winner
    f = filed_rates(rows)
    assert set(f) == {"cpr", "cdr", "prorata"}
    cs = consistency(g, st.age0, f, {})
    assert cs["C"][np.argmin(np.abs(g.prepay_level - 0.06) + g.default_level + g.trigger)] < cs["C"].max()
    assert (cs["trigger_contradiction"][g.trigger == 1] == 1).all()
    rows2 = history(5, with_sub=False)
    for r in rows2:
        r["coll_end_balance_net"] = ""
    assert state_for(rows2, DEAL).mode == "proxy" and state_for([], DEAL) is None
    rows3 = [dict(r, coll_end_balance=1e9, wac=0.03) for r in rows2]
    assert state_for(rows3, DEAL).mode == "pool_sub_implied"
    assert history_trend(rows[:1]) is None and history_trend(rows[:2])["n"] == 2


def test_rank_and_criterion_are_deterministic_and_informative():
    rows = history(24, rate=0.10, noise=0.003, with_sub=False)
    res = rank_isin("X", rows, DEAL, SMALL, 98.5)
    ranking, marg, summ = res
    assert ranking[0]["rank"] == 1 and summ["state_mode"] == "proxy" and summ["filed_facts_used"] == "prorata"
    assert {m["dimension"] for m in marg} >= {"prepay", "default", "call"}
    assert repr(rank_isin("X", rows, DEAL, SMALL, 98.5)) == repr(res)
    crit = check("X", rows, DEAL, SMALL)
    assert len(crit) >= 10
    sp = np.nanmean([c["spearman_S_vs_error"] for c in crit])
    assert sp > 0.3                                                      # on a stable deal, smoother => closer
    assert np.median([c["err_smoothest_bp"] for c in crit]) < np.median([c["err_grid_median_bp"] for c in crit])
    assert rank_isin("X", [], DEAL, SMALL, 98.5) is None
    assert np.isnan(spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0])))
    assert np.isnan(spearman(np.ones(5), np.arange(5.0)))


def test_bbg_replication():
    rows = history(10, with_sub=False)
    assert replicate("X", rows, DEAL, {"status": "MANUAL_REQUIRED: missing"})[0]["status"].startswith("MANUAL")
    run = {"run_id": "r1", "isin": "X", "settlement_date": rows[5]["payment_date"], "cpr": "10", "cdr": "0",
           "severity": "35", "recovery_lag": "12", "call_assumption": "maturity"}
    # 'Bloomberg' output generated with the same engine and conventions -> engine_only must match to the cent
    st = state_for(rows, DEAL, upto=6)
    g = grid1(prepay_level=0.10, wac=0.03, wam=240.0)
    g.wac[:] = 0.03
    stp = E.State(st.start, st.pool, st.a, st.sub, st.pdl, st.age0, st.index, st.prorata_filed, st.mode)
    pr = E.run(DEAL, stp, g, horizon_q=6)
    cfs = [{"run_id": "r1", "payment_date": d.isoformat(), "ending_balance": str(e), "scheduled_principal": "", "prepayment": "",
            "default": ""} for d, e in zip(pr["dates"], pr["a_end"][0])]
    cfs[0]["scheduled_principal"] = str(pr["a_prin"][0, 0])
    out = replicate("X", rows, DEAL, {"status": "ok", "runs": {"r1": run}, "cashflows": cfs})
    eo = next(o for o in out if o["variant"] == "engine_only")
    assert eo["status"] == "ok" and eo["max_abs_diff_eur"] < 0.01
    assert replicate("Y", rows, DEAL, {"status": "ok", "runs": {"r1": run}, "cashflows": cfs})[0]["status"].startswith("MANUAL")
