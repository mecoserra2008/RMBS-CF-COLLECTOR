import pytest

from helpers import DEAL, history
from rmbs import deals as D
from rmbs import gate as G
from rmbs import history as Hs


def chain_for(rows):
    return {r["payment_date"]: "PASS" for r in rows[1:]}


def test_gate_pass_on_complete_filings():
    rows = history(14)
    res = G.replay(rows, DEAL, chain_for(rows))
    assert res["n_replayed"] == 12
    assert (res["G1"], res["G2"], res["G3"], res["G4"], res["G5"], res["G6"]) == ("PASS",) * 4 + ("N/A", "PASS")
    assert res["gate_status"] == "PASS"


def test_gate_failures_and_incomplete():
    rows = history(14)
    rows[-1]["interest_paid"] += 50
    assert G.replay(rows, DEAL, chain_for(rows))["gate_status"] == "BLOCKED_FOR_PRICING"
    rows = history(14)
    rows[-1]["principal_paid"] += 1e6
    rows[-1]["end_balance"] -= 1e6
    assert G.replay(rows, DEAL, chain_for(rows))["G1"] == "FAIL"
    rows = history(14)
    rows[-2]["sub_end_balance"] = rows[-2]["sub_beg_balance"]          # sub unpaid while rule says pro-rata
    rows[-2]["principal_paid"] += 1
    res = G.replay(rows, DEAL, chain_for(rows))
    assert res["G4"] == "FAIL"
    rows = history(14, with_sub=False)
    res = G.replay(rows, DEAL, {**chain_for(rows), rows[-1]["payment_date"]: "BREAK"})
    assert res["G1"] == "INCOMPLETE" and res["G3"] == "FAIL" and res["gate_status"] == "BLOCKED_FOR_PRICING"
    rows = history(14)
    rows[-1]["reserve_balance"] = 5e6
    assert G.replay(rows, DEAL, chain_for(rows))["G6"] == "FAIL"
    rows = history(14)
    rows[-1]["principal_due"] = rows[-1]["principal_paid"] + 1e6
    res = G.replay(rows, DEAL, chain_for(rows))
    assert res["G5"] == "INCOMPLETE" and res["gate_status"] == "INCOMPLETE"
    assert G.replay([], DEAL, {})["gate_status"] == "NO_HISTORY"


def test_gate_uci_style_coupon_not_filed():
    rows = history(3)
    for r in rows:
        r["next_index_rate"] = r["index_rate"]
    deal = dict(DEAL, coupon_filed=False, interest_rounding="per_note")
    assert G.replay(rows, deal, chain_for(rows))["G2"] == "FAIL"     # synthetic interest is not per-note rounded
    for r in rows:
        n = r["n_notes"]
        r["interest_paid"] = round(r["beg_balance"] / n * r["coupon_rate"] * r["accrual_days"] / 360, 2) * n
    res = G.replay(rows, deal, chain_for(rows))
    assert res["G2"] == "INCOMPLETE"          # first row has no previous fixing, the others pass
    assert G._agg([], True) == "N/A" and G._agg(["PASS", "N/A"], True) == "PASS" and G._agg(["N/A"], True) == "N/A"


def test_deal_modules():
    r = {"prorata_test": "PASS"}
    f = {"prorata_test": "FAIL (B,C not amortised)"}
    for fam in ("bcp", "santander", "edt"):
        d = {"family": fam}
        assert D.mode(d, r) == "pro_rata" and D.mode(d, f) == "sequential" and D.mode(d, {}) is None
    assert D.mode({"family": "tda", "amortisation": {"mode": "sequential"}}, r) == "sequential"
    t8 = {"family": "tda", "amortisation": {"mode": "pro_rata_while_pool_factor_ge"}}
    assert D.mode(t8, r) == "pro_rata" and D.mode(t8, f) == "sequential" and D.mode(t8, {}) is None
    assert D.mode({"family": "tda"}, r) is None
    assert D.mode({"family": "x", "amortisation": {"mode": "sequential"}}, r) == "sequential"
    assert D.mode({"family": "x"}, r) is None
    assert D.shortfall_allowed({"family": "edt", "principal_shortfall_option": True})
    assert not any(D.shortfall_allowed({"family": f}) for f in ("bcp", "tda", "santander", "x"))


def test_history_merge_write_load(tmp_path):
    rows = history(3)
    Hs.write(tmp_path, "XS0222684655", rows)
    back = Hs.load(tmp_path, "XS0222684655")
    assert [r["payment_date"] for r in back] == [r["payment_date"] for r in rows]
    assert back[0]["n_notes"] == 10000 and isinstance(back[0]["beg_balance"], float)
    worse = dict(rows[0], parse_status="review: x", beg_balance="", wac=0.03)
    better = dict(rows[1], parse_status="ok: better", source_url="v")
    rows[1]["parse_status"] = "DERIVED (x)"
    merged, log = Hs.merge(rows, [worse, better, dict(rows[2], payment_date="2030-01-01")])
    assert merged[0]["parse_status"].startswith("ok") and merged[0]["wac"] == 0.03       # blank filled, status kept
    assert merged[1]["parse_status"] == "ok: better" and any(m.startswith("upgraded") for m in log)
    assert len(merged) == 4 and Hs.status_rank("weird") == 9
    assert Hs.load(tmp_path, "XS0260784318") == []
    assert Hs.coerce({"n_notes": "abc"})["n_notes"] == "abc"
