"""Golden-file test of the whole pipeline on a 3-ISIN subset + determinism (two runs byte-identical) + the
champion -> pricing path on a synthetic deal."""
import hashlib
import json
import shutil
from pathlib import Path

import yaml

from conftest import ROOT
from helpers import DEAL, history
from rmbs import history as Hs
from rmbs import run as R
from rmbs import selfcheck as SC

SUBSET = ["XS0222684655", "ES0377992005", "ES0345672010"]
GOLDEN = ROOT / "tests" / "golden" / "pipeline_3isin.json"


def make_root(tmp: Path, isins=SUBSET) -> Path:
    for d in ("config", "captures"):
        shutil.copytree(ROOT / d, tmp / d)
    (tmp / "out").mkdir()
    for p in (ROOT / "out").glob("*.csv"):
        if p.name == "yearend_checkpoints.csv" or p.name[:12] in isins:
            shutil.copy(p, tmp / "out" / p.name)
    (tmp / "tests" / "golden").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "golden" / "reprice.json", tmp / "tests" / "golden" / "reprice.json")
    shutil.copy(ROOT / "filing_locations.csv", tmp / "filing_locations.csv")
    cfg = yaml.safe_load((ROOT / "config" / "run.yaml").read_text())
    cfg.update(network=False, only=isins)
    cfg["scoring"].update(draws_backtest=40, draws_fan=100, fan_steps=12)
    (tmp / "config" / "run.yaml").write_text(yaml.safe_dump(cfg))
    return tmp


def digest(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def run(root: Path) -> int:
    return R.main(["--config", str(root / "config" / "run.yaml"), "--offline"])


def summary(root: Path) -> dict:
    import csv
    gate = {r["isin"]: r["gate_status"] for r in csv.DictReader(open(root / "out" / "reproduction_gate.csv"))}
    champ = {r["isin"]: r["status"] for r in csv.DictReader(open(root / "out" / "champion_models.csv"))}
    rows = {i: len(Hs.load(root / "out", i)) for i in SUBSET}
    gaps = sum(1 for _ in open(root / "out" / "gaps.csv")) - 1
    ye = [r["result"] for r in csv.DictReader(open(root / "out" / "yearend_reconciliation.csv"))]
    return {"gate": gate, "champion": champ, "rows": rows, "gaps": gaps, "yearend": sorted(ye)}


def test_golden_and_determinism(tmp_path):
    a = make_root(tmp_path / "a")
    assert run(a) == 0
    first = digest(a / "out")
    assert run(a) == 0                                   # resumable / idempotent re-run on the same root
    assert digest(a / "out") == first                    # two consecutive runs byte-identical
    s = summary(a)
    if not GOLDEN.exists():                              # pragma: no cover - first-time golden creation
        GOLDEN.write_text(json.dumps(s, indent=1, sort_keys=True))
    assert s == json.loads(GOLDEN.read_text())
    assert (a / "reports" / "index.html").exists() and (a / "COVERAGE.md").read_text().startswith("# Coverage")
    assert SC.main(["--config", str(a / "config" / "run.yaml")]) == 0


def test_champion_priced_on_synthetic_deal(tmp_path):
    root = make_root(tmp_path / "s", isins=["XS0222684655"])
    deal = dict(DEAL, isin="XS0222684655")
    (root / "config" / "deals" / "XS0222684655.yaml").write_text(yaml.safe_dump(deal))
    rows = history(40, rate=0.10, noise=0.002)
    for r in rows:
        r["isin"] = "XS0222684655"
    Hs.write(root / "out", "XS0222684655", rows)
    shutil.rmtree(root / "captures")
    (root / "out" / "yearend_checkpoints.csv").write_text("fund,isin_or_fund_senior,date,senior_notes_outstanding_kEUR,scope,source_url\n")
    cfg = yaml.safe_load((root / "config" / "run.yaml").read_text())
    cfg["scoring"]["gates"]["A6_win_share_min"] = 0.0
    cfg["stages"] = ["parse", "validate", "gate", "select", "price", "bbg", "report"]
    (root / "config" / "run.yaml").write_text(yaml.safe_dump(cfg))
    assert run(root) == 0
    import csv
    ch = next(csv.DictReader(open(root / "out" / "champion_models.csv")))
    pr = next(csv.DictReader(open(root / "out" / "pricing.csv")))
    assert ch["status"].startswith("CHAMPION") and pr["status"] == "PRICED"
    assert float(pr["wal_to_call"]) <= float(pr["wal_to_maturity"]) and float(pr["dm_bp"]) > 0


def test_stage_failure_sets_exit_code(tmp_path, monkeypatch):
    root = make_root(tmp_path / "f", isins=["ES0377992005"])
    monkeypatch.setattr(R.Run, "gate", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert R.main(["--config", str(root / "config" / "run.yaml"), "--offline", "--stages", "validate,gate"]) == 1
    assert "FAILED" in (root / "out" / "stage_status.csv").read_text()


def test_selfcheck_catches_bad_outputs(tmp_path):
    root = make_root(tmp_path / "c", isins=["ES0377992005"])
    rows = Hs.load(root / "out", "ES0377992005")
    rows[0]["end_balance"] += 5000
    rows[1]["payment_date"] = "2099-01-01"
    Hs.write(root / "out", "ES0377992005", rows)
    (root / "out" / "gaps.csv").write_text("bad\n")
    cfg = yaml.safe_load((root / "config" / "run.yaml").read_text())
    errs = SC.check(root, cfg)
    assert any("violates" in e for e in errs) and any("future" in e for e in errs) and any("gaps.csv" in e for e in errs)
    (root / "tests" / "golden" / "reprice.json").write_text(json.dumps([{"isin": "ES0377992005", "start": "2022-10-26",
        "balance": 1e8, "cpr": 0.2, "index": 0.02, "price": 98.5, "dm_bp": 0.0, "wal": 0.0}]))
    assert any("reprice" in e for e in SC.reprice_cases(root))
    (root / "tests" / "golden" / "reprice.json").unlink()
    assert SC.reprice_cases(root) == ["missing tests/golden/reprice.json"]


def test_cli_dispatch(monkeypatch):
    from rmbs import cli
    assert cli.main(["nope"]) == 2
    called = []
    monkeypatch.setattr("rmbs.selfcheck.main", lambda argv: called.append(argv) or 0)
    assert cli.main(["selfcheck", "--config", "x"]) == 0 and called == [["--config", "x"]]
