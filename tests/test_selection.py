"""Candidates, rolling-origin backtest, leakage (A7), composite score and champion on a synthetic deal."""
import copy
import datetime as dt
import math

import pytest

from helpers import DEAL, history
from rmbs.selection import backtest as BT
from rmbs.selection import bootstrap as B
from rmbs.selection import metrics as M
from rmbs.selection.candidates import CPR_MODELS, Candidate, Obs, grid, key_param
from rmbs.selection.score import evaluate_isin, leakage_check, summarise

CFG = {"valuation_date": "2026-09-23", "seed": 1, "scoring": {"price_ref": 98.5, "draws_backtest": 40, "draws_fan": 60, "fan_steps": 12}}


def obs_series(n=30, rate=0.1, amp=0.0):
    return [Obs(f"2010-{1 + i % 12:02d}-01", f"2010-{1 + i % 12:02d}-02", rate + amp * math.sin(i), 3.0 * i, 0.02 + 0.001 * (i % 5), "X")
            for i in range(n)]


def test_every_candidate_fits_and_projects():
    obs = obs_series(30, amp=0.02)
    ctx = {"pooled_obs": obs_series(20, rate=0.08, amp=0.01), "step_months": 3}
    for name, m in CPR_MODELS.items():
        p = m.fit(obs, ctx)
        assert p is not None, name
        for k in (1, 4, 12):
            assert 0 <= m.path(p, k) < 1, name
        assert not math.isnan(key_param(name, p)) or name == "cpr_regime"


def test_candidates_not_identifiable_on_short_data():
    short = obs_series(2)
    for name in ("cpr_ma3", "cpr_ewma", "cpr_ramp", "cpr_rate", "cpr_ar1", "cpr_regime"):
        assert CPR_MODELS[name].fit(short, {}) is None
    assert CPR_MODELS["cpr_season"].fit(short, {"pooled_obs": []}) is None
    assert CPR_MODELS["cpr_last"].fit([], {}) is None
    assert CPR_MODELS["cpr_pooled"].fit([], {"pooled_obs": obs_series(5)})["w"] == 0.0
    assert CPR_MODELS["cpr_pooled"].fit(short, {"pooled_obs": []})["w"] == 1.0
    flat = [Obs("d", "d", 0.1, 3.0, None, "X")] * 30
    assert CPR_MODELS["cpr_regime"].fit(flat, {}) is None
    assert CPR_MODELS["cpr_season"].fit(short, {"pooled_obs": [Obs("d", "d", 0.1, 5.0, None, "Y")] * 10}) is None


def test_grid():
    g = grid(DEAL, thin=False)
    assert len(g) == len(CPR_MODELS) * 2 and all(isinstance(c, Candidate) for c in g)
    g = grid(dict(DEAL, call_issuer_stated="2030-01-01"), thin=True, has_default_series=True)
    assert {c.cpr for c in g} == {"cpr_pooled"} and {c.cdr for c in g} == {"cdr_zero", "cdr_last", "cdr_ma12"}
    assert len(g) == 9 and g[0].name.count("|") == 3


def test_bootstrap_and_metrics():
    p1 = B.block_paths([0.1, -0.1, 0.2], 7, 5, seed=3)
    assert p1 == B.block_paths([0.1, -0.1, 0.2], 7, 5, seed=3) and len(p1[0]) == 7
    assert B.block_paths([], 3, 2, 1) == [[0.0] * 3] * 2
    assert B.quantile([1, 2, 3, 4], 0.5) == 2.5 and math.isnan(B.quantile([], 0.5))
    assert B.crps([1.0] * 10, 1.0) == 0 and B.crps([0.0, 2.0], 1.0) == pytest.approx(0.5) and math.isnan(B.crps([], 1))
    assert M.rmse([3, 4]) == pytest.approx(math.sqrt(12.5)) and math.isnan(M.rmse([]))
    assert M.zscores([1.0, 2.0, 3.0])[0] < 0 and M.zscores([1.0]) == [0.0] and M.zscores([1.0, float("nan"), 1.0])[1] == 0.0
    assert math.isnan(M.stab([1.0])) and M.stab([1.0, 3.0]) == pytest.approx(0.5) and math.isnan(M.stab([-1.0, 1.0]))
    assert math.isnan(M.nanmean([float("nan")]))


def test_available_dates_respect_filing_lag():
    r = history(1)[0]
    assert BT.available_date(r, DEAL) == dt.date.fromisoformat(r["payment_date"]) + dt.timedelta(days=5)
    r["source_section"] = "Cuentas Anuales (kEUR)"
    assert BT.available_date(r, DEAL) == dt.date(2011, 4, 30)
    assert BT.available_date(r, {"publication_lag_days": None}) == dt.date(2011, 4, 30)


def test_leakage_future_data_cannot_move_a_forecast():
    rows = history(30)
    obs = BT.observations("XS_SYNTH", rows, DEAL)
    cut = dt.date.fromisoformat(rows[15]["payment_date"]) - dt.timedelta(days=1)
    for name in CPR_MODELS:
        c = Candidate(name, "cdr_zero", "rec_none", "call_never")
        f0 = BT.forecast_at(c, rows, obs, obs, DEAL, cut)
        rows2 = copy.deepcopy(rows)
        for r in rows2[15:]:
            r["principal_paid"] *= 3
            r["end_balance"] *= 0.5
        obs2 = BT.observations("XS_SYNTH", rows2, DEAL)
        f1 = BT.forecast_at(c, rows2, obs2, obs2, DEAL, cut)
        assert (f0 is None) == (f1 is None)
        if f0:
            assert [p.end for p in f0[2].periods] == [p.end for p in f1[2].periods], name


def test_backtest_champion_on_stable_synthetic_deal():
    rows = history(40, rate=0.10, noise=0.004)
    res = evaluate_isin("XS_SYNTH", rows, DEAL, [], "PASS", CFG)
    ch = res["champion"]
    assert ch["n_origins"] >= 20 and ch["E_fac_h1"] < 25
    assert res["scores"] and {s["h"] for s in res["scores"]} == {1, 4, 8, 12}
    assert "A1" in ch["gates_passed"] and "A7" in ch["gates_passed"]
    assert res["fan"] and res["fan"][-1]["metric"] == "dm_bp_at_price_ref"
    fan_bal = [f for f in res["fan"] if f["metric"] == "balance"]
    assert all(f["p5"] <= f["p50"] <= f["p95"] for f in fan_bal)
    again = evaluate_isin("XS_SYNTH", rows, DEAL, [], "PASS", CFG)
    assert repr(again["scores"]) == repr(res["scores"]) and repr(again["fan"]) == repr(res["fan"])   # deterministic


def test_selection_status_paths():
    rows = history(40, rate=0.10, noise=0.004)
    assert evaluate_isin("X", rows, {}, [], "PASS", CFG)["champion"]["gates_passed"].startswith("CONFIG_INCOMPLETE")
    assert "NO_HISTORY" in evaluate_isin("X", rows[:2], DEAL, [], "PASS", CFG)["status"]
    thin = evaluate_isin("X", rows[:8], DEAL, BT.observations("P", history(20, seed=3), DEAL), "INCOMPLETE", CFG)
    assert thin["thin"] and "THIN_HISTORY" in thin["champion"]["status"] and "A1" in thin["champion"]["gates_passed"].split("|")[-1]
    rows_gap = [r for i, r in enumerate(history(30)) if i % 2 == 0]
    r = evaluate_isin("X", rows_gap, DEAL, [], "PASS", CFG)
    assert r["champion"]["status"].startswith("NO_RELIABLE_MODEL")


def test_leakage_check_detects_a_leaky_model(monkeypatch):
    rows = history(30)
    obs = BT.observations("XS_SYNTH", rows, DEAL)
    ctx = BT.Context(deal=DEAL, rows=rows, pooled=[])
    c = Candidate("cpr_ma3", "cdr_zero", "rec_none", "call_never")
    cuts = [dt.date.fromisoformat(rows[12]["payment_date"])]
    assert leakage_check(c, ctx, obs, cuts)
    real_info = BT.info_rows
    monkeypatch.setattr(BT, "info_rows", lambda rows, deal, cut: rows)            # peek at everything
    assert not leakage_check(c, ctx, obs, cuts)
    monkeypatch.setattr(BT, "info_rows", real_info)


def test_summarise_and_window_helpers():
    s0 = dt.date(2020, 1, 1)
    dates = [s0 + dt.timedelta(days=91 * k) for k in (1, 2)]
    assert BT.window_wal(100, [50, 50], dates, s0) == pytest.approx((91 * 50 + 182 * 50) / 365 / 100)
    assert BT.window_dm(100, [50, 50], dates, s0, 0.03, 0.02, 100.0) == pytest.approx(0.01, abs=1e-6)
    assert summarise([], None) == {}
    assert BT.origins([], DEAL) == [] and BT.origins_thin([], DEAL) == []
    assert BT.step_months({}) == 3.0 and BT.validated([{"parse_status": "review"}]) == []
    assert BT.index_ref({"coupon_rate": ""}, {}) == (0.0, 0.0)
