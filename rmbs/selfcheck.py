"""Final self-check: re-validate every output CSV against its schema and the invariants, and re-price the stored
reference cases (tests/golden/reprice.json). Exit code 0 only if everything holds.

    python -m rmbs.selfcheck [--config config/run.yaml]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import config as C
from . import history as Hs
from .schema import TABLES, check_history_rows, check_table
from .sources import REGISTRY

TABLE_FILES = {"validation.csv": "validation", "gaps.csv": "gaps", "yearend_reconciliation.csv": "yearend_reconciliation",
               "reproduction_gate.csv": "reproduction_gate", "champion_models.csv": "champion_models",
               "bbg_vs_model.csv": "bbg_vs_model", "pricing.csv": "pricing", "location_checks.csv": "location_checks",
               "calendar_checks.csv": "calendar_checks"}


def reprice_cases(root: Path) -> list[str]:
    from .engine import Assumptions, cashflows, project, solve_dm, wal
    p = root / "tests" / "golden" / "reprice.json"
    if not p.exists():
        return ["missing tests/golden/reprice.json"]
    errs = []
    for case in json.loads(p.read_text()):
        deal = C.load_deal(case["isin"], root / "config" / "deals")
        a = Assumptions(rate_path=lambda k, r=case["cpr"]: r, index=case["index"])
        start = dt.date.fromisoformat(case["start"])
        cf = cashflows(project(deal, start, case["balance"], a))
        dm = solve_dm(cf, start, case["index"], case["price"] / 100 * case["balance"]) * 1e4
        w = wal(cf, start)
        if abs(dm - case["dm_bp"]) > 1e-4 or abs(w - case["wal"]) > 1e-6:
            errs.append(f"reprice {case['isin']}: dm {dm:.6f} vs {case['dm_bp']}, wal {w:.8f} vs {case['wal']}")
    return errs


def check(root: Path, cfg: dict) -> list[str]:
    out = Path(root) / cfg.get("out_dir", "out")
    vdate = dt.date.fromisoformat(str(cfg["valuation_date"]))
    errs = []
    for isin in REGISTRY:
        rows = Hs.load(out, isin)
        errs += [f"{isin}: {e}" for e in check_history_rows(rows)]
        last = None
        for r in rows:
            if r["payment_date"] > vdate.isoformat():
                errs.append(f"{isin}: future-dated row {r['payment_date']}")
            ok = str(r["parse_status"]).startswith(("ok", "DERIVED"))
            if ok and r["beg_balance"] != "" and r["principal_paid"] != "" and r["end_balance"] != "":
                tol = 1000.0 if Hs.is_kEUR(r) else 0.02
                if abs(float(r["beg_balance"]) - float(r["principal_paid"]) - float(r["end_balance"])) > tol:
                    errs.append(f"{isin} {r['payment_date']}: 'ok' row violates beg-principal=end")
                if float(r["principal_paid"]) < 0:
                    errs.append(f"{isin} {r['payment_date']}: negative principal")
                if last is not None and float(r["end_balance"]) > last + 1000:
                    errs.append(f"{isin} {r['payment_date']}: balance increased")
                last = float(r["end_balance"])
    for f, name in TABLE_FILES.items():
        p = out / f
        if p.exists():
            errs += check_table(p, name)
    for sub, name in (("model_scores", "model_scores"), ("uncertainty", "uncertainty")):
        for p in sorted((out / sub).glob("*.csv")) if (out / sub).exists() else []:
            errs += check_table(p, name)
    errs += reprice_cases(Path(root))
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rmbs.selfcheck")
    ap.add_argument("--config", default="config/run.yaml")
    a = ap.parse_args(argv)
    cfg = C.load_run(a.config)
    errs = check(Path(cfg["root"]), cfg)
    for e in errs:
        print("SELFCHECK:", e)
    print("selfcheck:", "ok" if not errs else f"{len(errs)} problem(s)")
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
