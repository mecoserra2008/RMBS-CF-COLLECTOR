import datetime as dt

import pytest

from rmbs import calendar as cal
from rmbs.schema import SCHEMA, SchemaError, check_history_rows, check_table, fmt, write_table


def row(**kw):
    r = {c: "" for c in SCHEMA}
    r.update(isin="X", payment_date="2020-01-01", source_url="u", source_section="s", parse_status="ok")
    r.update(kw)
    return r


def test_schema_extensions_appended():
    assert SCHEMA[:44][-1] == "parse_status"
    assert SCHEMA[44:] == ["principal_due", "principal_shortfall", "sub_beg_balance", "sub_end_balance", "coll_beg_balance_net"]


def test_check_history_rows():
    assert check_history_rows([row()]) == []
    errs = check_history_rows([row(beg_balance="abc", parse_status="weird"), row(payment_date="2019-01-01", source_url="")])
    assert any("not numeric" in e for e in errs)
    assert any("not a known status" in e for e in errs)
    assert any("missing source_url" in e for e in errs)
    assert "rows not sorted by payment_date" in errs
    assert "duplicate payment_date" in check_history_rows([row(), row()])
    assert "columns differ" in check_history_rows([{"a": 1}])[0]


def test_fmt_and_tables(tmp_path):
    assert fmt(None) == "" and fmt(True) == "True" and fmt(-0.0) == "0.0" and fmt(float("nan")) == ""
    assert fmt(832103561.25) == "832103561.25" and fmt(0.1 + 0.2) == "0.3" and fmt(7) == "7"
    p = tmp_path / "g.csv"
    write_table(p, "gaps", [{"isin": "X", "expected_ipd": "2020-01-01"}])
    assert check_table(p, "gaps") == []
    p.write_text("bad,header\n")
    assert check_table(p, "gaps")
    p.write_text("isin,ticker,expected_ipd,prev_observed,next_observed,status\na,b\n")
    assert "fields" in check_table(p, "gaps")[0]


def test_write_table_raises_on_bad_output(tmp_path, monkeypatch):
    import rmbs.schema as S
    monkeypatch.setattr(S, "check_table", lambda p, n: ["boom"])
    with pytest.raises(SchemaError):
        S.write_table(tmp_path / "x.csv", "gaps", [])


def test_easter_and_holidays():
    assert cal.easter(2024) == dt.date(2024, 3, 31) and cal.easter(2026) == dt.date(2026, 4, 5)
    assert dt.date(2013, 8, 15) in cal.holidays(2013, "PT")
    assert dt.date(2014, 12, 1) not in cal.holidays(2014, "PT")            # suspended 2013-2015
    assert dt.date(2016, 12, 1) in cal.holidays(2016, "PT")
    assert not cal.is_business_day(dt.date(2026, 5, 1))


@pytest.mark.parametrize("observed", ["2009-02-16", "2013-08-16", "2017-08-16", "2018-11-15", "2020-11-16",
                                      "2025-02-17", "2025-08-18", "2026-05-15", "2026-08-17"])
def test_magellan3_calendar_reproduces_observed_ipds(observed):
    rule = [{"months": [2, 5, 8, 11], "day": 15, "convention": "following", "calendar": "PT"}]
    o = dt.date.fromisoformat(observed)
    assert cal.next_ipds(rule, o - dt.timedelta(days=20), 1)[0] == o


def test_adjust_conventions_and_rules():
    sat = dt.date(2026, 1, 31)
    assert cal.adjust(sat, "following") == dt.date(2026, 2, 2)
    assert cal.adjust(sat, "modified_following") == dt.date(2026, 1, 30)
    assert cal.adjust(sat, "none") == sat
    rules = [{"months": [1, 4, 7, 10], "day": 22}, {"from": "2017-01-01", "months": [1, 4, 7, 10], "day": 15}]
    assert cal.rule_at(rules, dt.date(2016, 5, 1))["day"] == 22
    assert cal.rule_at(rules, dt.date(2018, 5, 1))["day"] == 15
    assert cal.next_ipds(rules, dt.date(2016, 12, 20), 2) == [dt.date(2017, 1, 16), dt.date(2017, 4, 18)]
    assert cal.year_frac(dt.date(2020, 1, 1), dt.date(2020, 4, 1)) == pytest.approx(91 / 360)
    assert cal.year_frac(dt.date(2020, 1, 1), dt.date(2020, 4, 1), "ACT/365") == pytest.approx(91 / 365)
