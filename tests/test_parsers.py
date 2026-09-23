"""One fixture per layout, exact numbers asserted (MODEL_SELECTION_PROTOCOL s.7.8)."""
import pytest

from conftest import fx
from rmbs.parsers import detect, parse_any, review_row
from rmbs.parsers import bcp, edt_accounts, santander_accounts, tda_accounts, tda_notice, uci

M3, M4, U15, H11, T5, T8 = "XS0222684655", "XS0260784318", "ES0380957003", "ES0345672010", "ES0377992005", "ES0377966009"

BCP_CASES = [  # file, isin, date, beg, principal, end, factor, coupon, interest
    ("magel3_2009-02.txt", M3, "2009-02-16", 832103561.25, 34219818.75, 797883742.50, 0.564374, 0.04375, 9202256.40),
    ("magel3_2025-11.txt", M3, "2025-11-17", 127626281.25, 4075841.25, 123550440.00, 0.087392, 0.02296, None),
    ("magel3_2026-05.txt", M3, "2026-05-15", 118524558.75, 3925983.75, 114598575.00, 0.08106, 0.02244, None),
    ("magel3_2026-08.txt", M3, "2026-08-17", 114598575.00, 3753506.25, 110845068.75, 0.078405, 0.02512, 751664.79),
    ("magel4_2026-01.txt", M4, "2026-01-20", 149957876.25, 5915130.00, 144042746.25, 0.101887, 0.02284, 875287.46),
    ("magel4_2026-07.txt", M4, "2026-07-20", 139333545.00, 4089978.75, 135243566.25, 0.095663, 0.02518, 886850.27),
]


@pytest.mark.parametrize("f,isin,d,beg,prin,end,fac,cpn,intr", BCP_CASES)
def test_bcp_layout(f, isin, d, beg, prin, end, fac, cpn, intr):
    t = fx(f)
    assert detect(t) == "bcp_investor_report"
    (r,) = bcp.parse(t, isin, "u")
    assert r["payment_date"] == d
    assert r["beg_balance"] == pytest.approx(beg, abs=0.005)
    assert r["principal_paid"] == pytest.approx(prin, abs=0.005)
    assert r["end_balance"] == pytest.approx(end, abs=0.005)
    assert r["pool_factor"] == pytest.approx(fac, abs=1e-9)
    assert r["coupon_rate"] == pytest.approx(cpn, abs=1e-9)
    if intr is not None:
        assert r["interest_paid"] == pytest.approx(intr, abs=0.005)
    assert r["n_notes"] == 141375
    assert r["parse_status"].startswith("ok")


def test_bcp_subordinate_balances_2026_08():
    (r,) = bcp.parse(fx("magel3_2026-08.txt"), M3, "u")
    assert r["sub_beg_balance"] == pytest.approx(3441892.50 + 1606216.50 + 3747838.50)
    assert r["sub_end_balance"] == pytest.approx(3329167.50 + 1553611.50 + 3625093.50)
    assert r["coll_beg_balance_net"] == pytest.approx(123397891.75)
    assert r["prorata_test"] == "PASS"
    assert r["reserve_balance"] == pytest.approx(9000000.0)


def test_bcp_isin_mismatch_is_review():
    (r,) = bcp.parse(fx("magel3_2026-08.txt"), "XS0000000000", "u")
    assert r["parse_status"].startswith("review: MISMATCH")


@pytest.mark.parametrize("f,d,beg,prin,end,intr", [
    ("uci15_2012-06.txt", "2012-06-18", 649794450.52, 8444305.34, 641350145.18, 1627756.52),
    ("uci15_2026-06.txt", "2026-06-18", 88627468.18, 6827005.50, 81800462.68, 520286.86)])
def test_uci_layout(f, d, beg, prin, end, intr):
    t = fx(f)
    assert detect(t) == "uci_informacion_periodica"
    (r,) = uci.parse(t, U15, "u", series="A")
    assert (r["payment_date"], r["n_notes"]) == (d, 13406)
    assert r["beg_balance"] == pytest.approx(beg, abs=0.01)
    assert r["principal_paid"] == pytest.approx(prin, abs=0.01)
    assert r["end_balance"] == pytest.approx(end, abs=0.01)
    assert r["interest_paid"] == pytest.approx(intr, abs=0.005)
    assert r["parse_status"].startswith("ok")


def test_uci_other_series_routed_to_layout_queue():
    (r,) = uci.parse(fx("uci15_2026-06.txt"), "ES0338186010", "u", series="A2")
    assert r["parse_status"].startswith("review: LAYOUT_UNKNOWN")


def test_uci_2026_trigger_and_pool():
    (r,) = uci.parse(fx("uci15_2026-06.txt"), U15, "u", series="A")
    assert r["prorata_test"].startswith("FAIL")
    assert r["coll_end_balance"] == pytest.approx(180964103.00)
    assert r["arrears_90_365"] == pytest.approx(16277027.60)
    assert r["next_coupon_rate"] == pytest.approx(0.02548)


@pytest.mark.parametrize("f,n,last_end", [("hipo11_ca2016.txt", 8, 302475000), ("hipo11_ca2019.txt", 8, 217021000)])
def test_edt_accounts_layouts(f, n, last_end):
    t = fx(f)
    assert detect(t) == "edt_accounts"
    rows = edt_accounts.parse(t, H11, "u")
    assert len(rows) == n
    assert round(rows[-1]["end_balance"]) == last_end
    assert rows[0]["tranche"] == "A2"


def test_edt_accounts_series_a3_and_unmapped():
    rows = edt_accounts.parse(fx("hipo11_ca2019.txt"), H11, "u", series="A3")
    assert rows[0]["principal_paid"] == pytest.approx(5807000)          # 15.01.2018 Serie A3 (5.807) kEUR
    (r,) = edt_accounts.parse(fx("hipo11_ca2019.txt"), "ES0000000001", "u")
    assert r["parse_status"].startswith("review: SERIES_UNMAPPED")
    (r,) = edt_accounts.parse("Serie A2 Serie A3\nnothing", H11, "u")
    assert r["parse_status"].startswith("review: LAYOUT_UNKNOWN")


def test_edt_due_paid_table():
    txt = fx("hipo11_ca2019.txt") + "\nInsuficiencia fondos disponibles\n15.01.2019 Serie A2 57.491 2.910 54.581\n"
    rows = edt_accounts.parse(txt, H11, "u")
    r = next(x for x in rows if x["payment_date"] == "2019-01-15")
    assert r["principal_due"] == 57491000 and r["principal_shortfall"] == 54581000


def test_tda_accounts_layout():
    t = fx("tdacam5_ca2022.txt")
    assert detect(t) == "tda_accounts"
    rows = tda_accounts.parse(t, T5, "u", series="A")
    assert len(rows) == 8
    assert rows[0]["payment_date"] == "2021-01-27" and rows[0]["beg_balance"] == 302596000
    assert abs(rows[-1]["end_balance"] - 210062000) <= 3000
    assert rows[-1]["interest_paid"] == 149000


def test_tda_accounts_guards():
    (r,) = tda_accounts.parse(fx("tdacam5_ca2022.txt"), T5, "u", series=None)
    assert r["parse_status"].startswith("review: SERIES_UNMAPPED")
    (r,) = tda_accounts.parse(fx("tdacam5_ca2022.txt"), T5, "u", series="A3")
    assert r["parse_status"].startswith("review: LAYOUT_UNKNOWN")


def test_tda_notice_partial():
    t = fx("tdacam8_2020-11.txt")
    assert detect(t) == "tda_notice"
    (r,) = tda_notice.parse(t, T8, "u")
    assert r["payment_date"] == "2020-11-26" and r["n_notes"] == 16354
    assert r["beg_balance"] == pytest.approx(306555402.92)
    assert r["original_balance"] == pytest.approx(1635400000.0)
    assert r["parse_status"].startswith("PARTIAL")
    (r,) = tda_notice.parse(t, "ES0000000000", "u")
    assert r["parse_status"].startswith("review: MISMATCH")


def test_santander_accounts_and_unknown():
    t = "UCI 16 Fondo de Titulizacion Cuentas Anuales 2023"
    assert detect(t) == "santander_accounts"
    layout, (r,) = parse_any(t, "ES0338186010", "u", series="A2")
    assert r["parse_status"].startswith("review: LAYOUT_UNKNOWN")
    assert santander_accounts.parse(t, "x", "u")[0]["isin"] == "x"
    layout, (r,) = parse_any("random text", "X", "u")
    assert layout == "unknown" and r["parse_status"] == "review: LAYOUT_UNKNOWN"


def test_parse_any_never_raises():
    bad = "Magellan Mortgages No. 3 Report\nSecurity Level Information\nISIN XS0222684655\n"
    layout, (r,) = parse_any(bad, M3, "u")
    assert layout == "bcp_investor_report" and r["parse_status"].startswith("review:")
    assert review_row("I", "u", "x")["source_section"] == "n/a"
