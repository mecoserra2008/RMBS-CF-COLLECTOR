"""Downloader against recorded HTTP fixtures (stub transport), Bloomberg ingest/scoring, location checks."""
import datetime as dt
import json

import pytest

from helpers import history
from rmbs import locate as L
from rmbs.bbg import compare, ingest
from rmbs.extract import pdf_text
from rmbs.fetch import Fetcher, Response

PDF = b"%PDF-1.4 recorded fixture"


class Recorded:
    """Replays a recorded sequence of responses per URL (vcr-style)."""
    def __init__(self, tape):
        self.tape = {k: list(v) for k, v in tape.items()}
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        r = self.tape[url].pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(tmp_path, tape, **kw):
    sleeps = []
    t = [0.0]
    f = Fetcher(tmp_path / "cache", Recorded(tape), sleep=sleeps.append, clock=lambda: t[0], **kw)
    return f, sleeps


def test_download_cache_restated_and_etag(tmp_path):
    u = "https://h/a.pdf"
    f, sleeps = make(tmp_path, {u: [Response(200, PDF, {"ETag": "e1"}), Response(200, PDF + b"v2", {})]})
    rec = f.get(u, tmp_path / "r" / "a.pdf")
    assert rec["status"] == 200 and rec["event"] == "downloaded" and len(rec["sha256"]) == 64
    assert f.get(u, tmp_path / "r" / "a.pdf")["status"] == "CACHED"
    (tmp_path / "r" / "a.pdf").unlink()
    rec = f.get(u, tmp_path / "r" / "a.pdf")
    assert rec["event"] == "RESTATED" and f.transport.calls[-1][1] == {"If-None-Match": "e1"}
    assert json.loads((tmp_path / "cache" / "index.json").read_text())[u]["sha256"] == rec["sha256"]
    assert sleeps and sleeps[0] == pytest.approx(0.4)          # per-host spacing


def test_retry_backoff_not_pdf_and_host_degrade(tmp_path):
    ok, bad, html_ = "https://h/ok.pdf", "https://dead/x.pdf", "https://h/page.pdf"
    tape = {ok: [ConnectionError("reset"), Response(503, b"", {}), Response(200, PDF, {})],
            html_: [Response(200, b"<html>", {})], bad: [ConnectionError("403 CONNECT")] * 9}
    f, sleeps = make(tmp_path, tape, retries=3)
    assert f.get(ok, tmp_path / "ok.pdf")["status"] == 200
    assert f.get(html_, tmp_path / "p.pdf")["event"] == "not a PDF"
    for i in range(3):
        assert f.get(bad, tmp_path / f"b{i}.pdf")["status"] == "SOURCE_UNAVAILABLE"
    assert f.dead_hosts() == {"dead"}
    assert f.get(bad, tmp_path / "b9.pdf")["event"] == "host degraded"
    assert len([c for c in f.transport.calls if c[0] == bad]) == 9
    off = Fetcher(tmp_path / "c2", None)
    assert off.get(ok, tmp_path / "zz.pdf")["event"] == "offline"


def test_extract_text_cache_and_fallback(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"not really a pdf")
    text, method = pdf_text(p, ocr=False)
    assert method in ("empty", "pdftotext") and (tmp_path / "x.txt").exists()
    assert pdf_text(p, ocr=False)[1] == "cache"


def test_bbg_missing_and_present(tmp_path):
    b = ingest.load(tmp_path)
    assert b["status"].startswith("MANUAL_REQUIRED")
    assert compare.score_runs("X", [], b, None, 1.0)[0]["status"].startswith("MANUAL_REQUIRED")
    rows = history(6)
    (tmp_path / "cashflows.csv").write_text(",".join(ingest.CF_COLS) + "\n" + "\n".join(
        f"XS_SYNTH,r1,{r['payment_date']},0,0,0,0,0,0,{r['end_balance'] + 1e6}" for r in rows[3:]) + "\n")
    b = ingest.load(tmp_path)
    assert b["status"].startswith("UNUSABLE")
    (tmp_path / "runs.csv").write_text(",".join(ingest.RUN_COLS) + "\nr1,XS_SYNTH,2011-01-01,REPLINES,,,,,,,,,,,,,v\n")
    b = ingest.load(tmp_path)
    assert b["status"] == "ok"
    out = compare.score_runs("XS_SYNTH", rows, b, {"E_fac_h1": 1.0}, 1e9)
    assert out[0]["bbg"] == pytest.approx(10.0) and out[0]["winner"] == "model"
    assert compare.score_runs("OTHER", rows, b, None, 1e9)[0]["status"].startswith("MANUAL_REQUIRED: no Bloomberg run")
    (tmp_path / "runs.csv").write_text("run_id\nr1\n")
    assert ingest.load(tmp_path)["status"].startswith("SCHEMA_ERROR")


def test_bbg_no_overlap(tmp_path):
    (tmp_path / "cashflows.csv").write_text(",".join(ingest.CF_COLS) + "\nX,r1,2040-01-01,0,0,0,0,0,0,5\n")
    (tmp_path / "runs.csv").write_text(",".join(ingest.RUN_COLS) + "\nr1,X,2039-01-01,REPLINES,,,,,,,,,,,,,v\n")
    assert compare.score_runs("X", history(2), ingest.load(tmp_path), None, 1.0)[0]["status"].startswith("NO_OVERLAP")


def test_replines_scaling(tmp_path):
    (tmp_path / "replines").mkdir()
    hdr = ",".join(ingest.REPLINE_COLS)
    (tmp_path / "replines" / "X.csv").write_text(hdr + "\nr1,60,,,,,,,,,,\nr2,40,,,,,,,,,,\n")
    r = ingest.replines_check(tmp_path, "X", 102.0)
    assert r["replines"]["status"] == "ok" and r["loanlevel"]["status"].startswith("MANUAL_REQUIRED")
    assert ingest.replines_check(tmp_path, "X", 150.0)["replines"]["status"].startswith("REFUSED")
    assert ingest.replines_check(tmp_path, "X", None)["replines"]["status"].startswith("UNUSABLE")


def test_locate_patterns_and_checks():
    names = list(L.bcp_names("Magellan4", 2026, 1))
    assert "Magellan-Mortgages-No4-plc_16012026.pdf" in names and "InvestorReport_202601.pdf" in names
    g = L.pattern_urls("XS0222684655", [(2026, 8)])
    assert any(u.endswith("Investor-Report-202608.pdf") for u in g[0])
    g = L.pattern_urls("ES0345672010", [(2016, 12)])
    assert g[0][0].endswith("EFGH11N01161231.pdf") and g[1][0].endswith("MFGH11C01161231.pdf")
    assert L.pattern_urls("ES0377992005", [(2020, 1)]) == []
    row = {"isin": "XS0222684655", "doc_type": "r", "period": "2026-08", "url": "u"}
    assert L.check_document(row, None, None)["status"] == "DEAD_OR_BLOCKED"
    txt = "Magellan Mortgages No. 3 Report August 2026\nSecurity Level Information\nXS0222684655"
    assert L.check_document(row, txt, "h")["status"] == "VALID"
    assert L.check_document(row, "Magellan Mortgages 2026", "h")["status"] == "LAYOUT_UNKNOWN"
    assert L.check_document(row, "other fund", "h")["status"] == "MISMATCH"
    assert L.check_document(dict(row, period="2019"), txt, "h")["L3"] == "CHECK_PERIOD"
    m = L.expected_months("X", {"ipd_rule": {"months": [2, 5, 8, 11], "day": 15}, "first_ipd": "2025-08-15"}, dt.date(2026, 3, 1))
    assert m == [(2025, 8), (2025, 11), (2026, 2)]
    assert L.expected_months("X", {}, dt.date(2026, 1, 1)) == []
    assert L.safe_name("https://a.b/c d?.pdf") == "a.b_c_d_.pdf"
