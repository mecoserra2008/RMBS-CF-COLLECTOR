"""Orchestrator: harvest -> parse -> validate -> gate -> select -> price -> bbg -> report -> selfcheck.

    python -m rmbs.run --config config/run.yaml [--stages validate,gate,...] [--offline]

Non-interactive, deterministic (seeds + valuation date in the config, no wall-clock in out/), resumable (downloads and
PDF text are cached; every stage rewrites its own outputs from files). Exit code 0 only if every stage's invariants held.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
import time
import traceback
from pathlib import Path

from . import config as C
from . import gate as G
from . import history as Hs
from . import locate as L
from . import validate as V
from .schema import SchemaError, write_table
from .sources import MANUAL, REGISTRY

STAGES = ["harvest", "parse", "validate", "gate", "select", "cft", "price", "bbg", "report", "workbook", "selfcheck"]


class Run:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.root = Path(cfg.get("root", C.ROOT))
        self.out = self.root / cfg.get("out_dir", "out")
        self.reports = self.root / cfg.get("reports_dir", "reports")
        self.vdate = dt.date.fromisoformat(str(cfg["valuation_date"]))
        self.deals = {i: C.load_deal(i, self.root / "config" / "deals") for i in REGISTRY}
        ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        self.logdir = self.root / cfg.get("log_dir", "logs")
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.logf = self.logdir / f"run_{ts}.jsonl"
        self.stage_status: list[dict] = []
        self.gate_res: dict[str, dict] = {}
        self.sel: dict[str, dict] = {}
        self.chain: dict[str, dict] = {}

    def log(self, stage: str, event: str, isin: str = "", **kw):
        rec = {"t": round(time.time(), 3), "stage": stage, "isin": isin, "event": event, **kw}
        with open(self.logf, "a", encoding="utf-8") as h:
            h.write(json.dumps(rec, sort_keys=True, default=str) + "\n")

    def isins(self):
        only = self.cfg.get("only") or []
        return [i for i in REGISTRY if not only or i in only]

    # ------------------------------------------------------------------ harvest
    def harvest(self):
        from .fetch import Fetcher, requests_transport
        net = bool(self.cfg.get("network", False))
        f = Fetcher(self.root / "cache" / "http", requests_transport() if net else None,
                    min_spacing=float(self.cfg.get("min_spacing_s", 0.4)))
        man = L.read_manifest(self.root / "filing_locations.csv")
        got = 0
        for isin in self.isins():
            if isin in MANUAL:
                continue
            folder = self.reports / f"{isin}_{REGISTRY[isin].ticker}"
            for r in man:
                if r["isin"] == isin and r["url"].lower().split("?")[0].endswith(".pdf"):
                    rec = f.get(r["url"], folder / L.safe_name(r["url"]))
                    got += rec["status"] in (200, "CACHED")
            if net and self.cfg.get("pattern_search", True):
                have = {x["report_month"] for x in Hs.load(self.out, isin) if str(x["parse_status"]).startswith("ok")}
                for grp in L.pattern_urls(isin, L.expected_months(isin, self.deals[isin], self.vdate)):
                    for u in grp:
                        if u.split("/")[-1][:6].isdigit() and u.split("/")[-1][:4] + "-" + u.split("/")[-1][4:6] in have:
                            break
                        rec = f.get(u, folder / L.safe_name(u))
                        if rec["status"] in (200, "CACHED"):
                            got += 1
                            break
                        if rec["status"] == "SOURCE_UNAVAILABLE":
                            break
        log = sorted(f.log, key=lambda r: (r["url"], str(r["status"])))
        with open(self.root / "download_log.csv", "w", newline="", encoding="utf-8") as h:
            w = csv.writer(h, lineterminator="\n")
            w.writerow(["url", "status", "bytes", "sha256", "event"])
            for r in log:
                w.writerow([r["url"], r["status"], r["bytes"], r["sha256"], r["event"]])
        dead = sorted(f.dead_hosts()) or sorted({r["url"].split("/")[2] for r in log if r["status"] == "SOURCE_UNAVAILABLE"})
        self.log("harvest", "done", ok=got, unavailable_hosts=dead)
        return f"{got} documents available; " + (f"SOURCE_UNAVAILABLE hosts: {','.join(dead)}" if dead else "all hosts reachable")

    # ------------------------------------------------------------------ parse
    def parse(self):
        from .extract import pdf_text
        from .parsers import parse_any
        from .sources import series_for
        man = L.read_manifest(self.root / "filing_locations.csv")
        by_name = {L.safe_name(r["url"]): r for r in man}
        idx_path = self.root / "cache" / "http" / "index.json"
        by_path = {}
        if idx_path.exists():
            for u, v in json.loads(idx_path.read_text()).items():
                by_path[Path(v["path"]).name] = u
        loc_rows, added = [], 0
        for isin in self.isins():
            folder = self.reports / f"{isin}_{REGISTRY[isin].ticker}"
            new = []
            for pdf in sorted(folder.glob("*.pdf")) if folder.exists() else []:
                text, method = pdf_text(pdf)
                url = by_path.get(pdf.name) or (by_name.get(pdf.name) or {}).get("url") or f"file:{pdf.relative_to(self.root)}"
                layout, rows = parse_any(text, isin, url, series=series_for(isin, self.deals[isin]))
                if isin in MANUAL:
                    rows = [dict(r, parse_status="review: MANUAL_REQUIRED (Citi report layout has no parser/fixture yet)")
                            if str(r["parse_status"]).startswith("review") else r for r in rows]
                new += rows
                self.log("parse", "parsed", isin, file=pdf.name, layout=layout, method=method, rows=len(rows))
            cap = self.root / "captures" / f"{isin}_{REGISTRY[isin].ticker}"
            for txt in sorted(cap.glob("*.txt")) if cap.exists() else []:
                # text captured from the issuer PDF when the PDF itself could not be stored (first line: '# source: <url>')
                raw = txt.read_text(encoding="utf-8")
                url = raw.split("\n", 1)[0].replace("# source:", "").strip()
                layout, rows = parse_any(raw, isin, url, series=series_for(isin, self.deals[isin]))
                new += [r for r in rows if r.get("payment_date")]
                self.log("parse", "parsed capture", isin, file=txt.name, layout=layout, rows=len(rows))
            cur = Hs.load(self.out, isin)
            merged, mlog = Hs.merge(cur, [r for r in new if r.get("payment_date")])
            if merged or Hs.path_for(self.out, isin).exists():
                Hs.write(self.out, isin, merged)          # also normalises every history file to the current SCHEMA
                added += sum(1 for m in mlog if m.startswith(("added", "upgraded")))
        # L1-L5 on every manifest row
        import hashlib
        for r in man:
            if r["isin"] not in REGISTRY:
                continue
            p = self.reports / f"{r['isin']}_{REGISTRY[r['isin']].ticker}" / L.safe_name(r["url"])
            text = p.with_suffix(".txt").read_text(encoding="utf-8") if p.with_suffix(".txt").exists() else None
            sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
            loc_rows.append(L.check_document(r, text, sha))
        loc_rows.sort(key=lambda x: (x["isin"], x["url"]))
        write_table(self.out / "location_checks.csv", "location_checks", loc_rows)
        valid = sum(1 for x in loc_rows if x["status"] == "VALID")
        return f"{added} rows added/upgraded; {valid}/{len(loc_rows)} manifest documents validated (L1-L5)"

    # ------------------------------------------------------------------ validate
    def expected(self, isin, rows):
        deal = self.deals[isin]
        if not C.ipd_rules(deal) or not rows:
            return []
        first = dt.date.fromisoformat(str(deal.get("first_ipd") or rows[0]["payment_date"]))
        last = self.vdate - dt.timedelta(days=int(self.cfg.get("latest_report_lag_days", 7)))
        return V.expected_ipds(deal, first, last)

    def validate(self):
        vrows, grows, yrows, crows = [], [], [], []
        cps = V.load_checkpoints(self.out / "yearend_checkpoints.csv")
        downgraded = 0
        for isin in self.isins():
            deal = self.deals[isin]
            rows = Hs.load(self.out, isin)
            for r in rows:
                if r.get("original_balance") in ("", None) and deal.get("original_balance"):
                    pass            # never back-fill filed rows from config
            exp = self.expected(isin, rows)
            ch = V.chain_checks(rows, exp)
            self.chain[isin] = {d: res for d, _, res, _ in ch}
            changed = False
            for r in rows:
                st = str(r["parse_status"])
                if st.startswith("review: FAIL") and "(was: " in st:     # re-evaluate from the filed status
                    r["parse_status"] = st.split("(was: ", 1)[1].rstrip(")")
                    changed = True
                checks = V.row_checks(r, deal, self.vdate)
                for name, res, det in checks:
                    vrows.append({"isin": isin, "payment_date": r["payment_date"], "check": name, "result": res, "detail": det})
                fails = [n for n, res, _ in checks if res == "FAIL"]
                if self.chain[isin].get(r["payment_date"]) == "BREAK":
                    fails.append("chain")
                if fails and str(r["parse_status"]).startswith(("ok", "DERIVED")):
                    r["parse_status"] = f"review: FAIL {','.join(fails)} (was: {r['parse_status']})"
                    changed = True
                    downgraded += 1
            for d, name, res, det in ch:
                vrows.append({"isin": isin, "payment_date": d, "check": name, "result": res, "detail": det})
            if changed:
                Hs.write(self.out, isin, rows)
            if isin not in MANUAL:
                if exp:
                    grows += V.gaps(isin, REGISTRY[isin].ticker, rows, exp)
                elif not rows:
                    grows.append({"isin": isin, "ticker": REGISTRY[isin].ticker, "expected_ipd": "", "prev_observed": "",
                                  "next_observed": "", "status": "NO_ROWS" + ("" if C.ipd_rules(deal) else "; CONFIG_INCOMPLETE: ipd_rule")})
            yrows += V.yearend(isin, rows, exp, cps)
            crows += V.calendar_checks(isin, rows, deal)
        key = lambda x: tuple(str(x.get(k, "")) for k in ("isin", "payment_date", "expected_ipd", "date", "observed", "check"))
        write_table(self.out / "validation.csv", "validation", sorted(vrows, key=key))
        write_table(self.out / "gaps.csv", "gaps", sorted(grows, key=key))
        write_table(self.out / "yearend_reconciliation.csv", "yearend_reconciliation", sorted(yrows, key=key))
        write_table(self.out / "calendar_checks.csv", "calendar_checks", sorted(crows, key=key))
        brk = sum(1 for x in vrows if x["result"] in ("FAIL", "BREAK"))
        ties = sum(1 for y in yrows if y["result"] == "TIED")
        ybrk = sum(1 for y in yrows if y["result"] == "BREAK")
        return f"{brk} failed checks, {downgraded} rows downgraded, {len(grows)} gaps, year-ends tied {ties}, broken {ybrk}"

    # ------------------------------------------------------------------ gate
    def gate(self):
        if not self.chain:
            for isin in self.isins():
                rows = Hs.load(self.out, isin)
                self.chain[isin] = {d: res for d, _, res, _ in V.chain_checks(rows, self.expected(isin, rows))}
        out = []
        for isin in self.isins():
            rows = Hs.load(self.out, isin)
            res = G.replay(rows, self.deals[isin], self.chain.get(isin, {}))
            if isin in MANUAL and not rows:
                res["gate_status"] = "MANUAL_REQUIRED"
                res["detail"] = "Citi investor reports not public; drop PDFs in reports/<ISIN>_LUSI_x/"
            self.gate_res[isin] = res
            out.append({"isin": isin, "ticker": REGISTRY[isin].ticker, **res})
        write_table(self.out / "reproduction_gate.csv", "reproduction_gate", out)
        from collections import Counter
        return "gate: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(r["gate_status"] for r in out).items()))

    # ------------------------------------------------------------------ select
    def select(self):
        from .selection.backtest import observations
        from .selection.score import evaluate_isin
        if not self.gate_res:
            self.gate()
        all_obs = {i: observations(i, Hs.load(self.out, i), self.deals[i]) for i in self.isins()}
        champs = []
        (self.out / "model_scores").mkdir(parents=True, exist_ok=True)
        (self.out / "uncertainty").mkdir(parents=True, exist_ok=True)
        for isin in self.isins():
            rows = Hs.load(self.out, isin)
            fk = REGISTRY[isin].fund_key
            pooled = [o for j, obs in all_obs.items() if REGISTRY[j].fund_key != fk for o in obs]
            res = evaluate_isin(isin, rows, self.deals[isin], pooled, self.gate_res[isin]["gate_status"], self.cfg)
            if isin in MANUAL and not rows:
                res["champion"]["status"] = "MANUAL_REQUIRED"
            self.sel[isin] = res
            champs.append({"isin": isin, **res["champion"]})
            write_table(self.out / "model_scores" / f"{isin}.csv", "model_scores", sorted(res["scores"], key=lambda r: (r["S"], r["candidate"], r["h"])))
            write_table(self.out / "uncertainty" / f"{isin}.csv", "uncertainty", res["fan"])
            self.log("select", "done", isin, status=res["status"])
        write_table(self.out / "champion_models.csv", "champion_models", champs)
        n_ch = sum(1 for c in champs if str(c.get("status", "")).startswith("CHAMPION"))
        return f"{n_ch} champions, {len(champs) - n_ch} NO_RELIABLE_MODEL/MANUAL"

    # ------------------------------------------------------------------ cft grid + smoothness ranking
    def cft(self):
        from .cft.run import run_all
        return run_all(self.root, [i for i in self.isins() if i not in MANUAL], self.cfg)

    # ------------------------------------------------------------------ price
    def price(self):
        from .engine import cashflows, project, solve_dm, spread_duration, wal
        from .selection.backtest import assumptions_for, index_ref, observations, validated
        from .selection.candidates import CPR_MODELS
        if not self.sel:
            self.select()
        price = float(self.cfg.get("scoring", {}).get("price_ref", 98.5))
        out = []
        for isin in self.isins():
            s = self.sel.get(isin, {})
            row = {"isin": isin, "ticker": REGISTRY[isin].ticker, "valuation_date": self.vdate.isoformat(), "price": price}
            ch = s.get("champion", {})
            if not str(ch.get("status", "")).startswith("CHAMPION"):
                g = self.gate_res.get(isin, {}).get("gate_status", "")
                row["status"] = ("BLOCKED_FOR_PRICING" if g == "BLOCKED_FOR_PRICING" else
                                 "NOT_PRICED: " + str(ch.get("status", "NO_RELIABLE_MODEL")))
                out.append(row)
                continue
            rows = validated(Hs.load(self.out, isin))
            deal = self.deals[isin]
            c = s["champion_candidate"]
            obs = observations(isin, rows, deal)
            params = CPR_MODELS[c.cpr].fit(obs, {"pooled_obs": [], "step_months": 3})
            st = rows[-1]
            idx, _ = index_ref(st, deal)
            start = dt.date.fromisoformat(st["payment_date"])
            B0 = float(st["end_balance"])
            res = {}
            for label, call in (("call", c.call), ("maturity", "call_never")):
                a = assumptions_for(type(c)(c.cpr, c.cdr, c.recovery, call), params, deal, st, idx)
                res[label] = cashflows(project(deal, start, B0, a))
            dm = solve_dm(res["call"], start, idx, price / 100 * B0)
            row.update(status="PRICED", balance=B0, dm_bp=dm * 1e4, wal_to_call=wal(res["call"], start),
                       wal_to_maturity=wal(res["maturity"], start), spread_duration=spread_duration(res["call"], start, idx, dm),
                       call_assumption=c.call, model=c.name)
            out.append(row)
        write_table(self.out / "pricing.csv", "pricing", out)
        return f"{sum(1 for r in out if r['status'] == 'PRICED')} ISINs priced"

    # ------------------------------------------------------------------ bbg
    def bbg(self):
        from .bbg import compare, ingest
        b = ingest.load(self.root / "bbg")
        out = []
        for isin in self.isins():
            rows = Hs.load(self.out, isin)
            orig = float(self.deals[isin].get("original_balance") or 1.0)
            out += compare.score_runs(isin, rows, b, self.sel.get(isin, {}).get("champion"), orig)
        write_table(self.out / "bbg_vs_model.csv", "bbg_vs_model", out)
        return "Bloomberg: " + b["status"]

    # ------------------------------------------------------------------ report
    def report(self):
        from . import report as R
        R.write_all(self)
        return "COVERAGE.md, docs/FINDINGS.md, reports/*.html written"

    def workbook(self):
        from . import workbook as W
        args = ["--config", str(self.root / "config" / "run.yaml")]
        if not self.cfg.get("workbook_recalc", True):
            args.append("--no-recalc")
        if W.main(args) != 0:
            raise RuntimeError("workbook: formula errors after recalculation (see log)")
        return "RMBS_CF_Valuation_Model.xlsx refreshed" + ("" if "--no-recalc" in args else ", LibreOffice recalc, 0 formula errors")

    def selfcheck(self):
        from .selfcheck import check
        errs = check(self.root, self.cfg)
        if errs:
            raise SchemaError("; ".join(errs[:10]))
        return "selfcheck ok"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rmbs.run")
    ap.add_argument("--config", default="config/run.yaml")
    ap.add_argument("--stages", default="")
    ap.add_argument("--offline", action="store_true", help="never touch the network (cached files only)")
    a = ap.parse_args(argv)
    cfg = C.load_run(a.config)
    if a.offline:
        cfg["network"] = False
    run = Run(cfg)
    stages = [s for s in (a.stages.split(",") if a.stages else cfg.get("stages", STAGES)) if s]
    ok = True
    for st in stages:
        t0 = time.time()
        try:
            detail = getattr(run, st)()
            status = "ok"
        except Exception as e:
            ok, status, detail = False, "FAILED", f"{type(e).__name__}: {e}"
            run.log(st, "error", trace=traceback.format_exc()[-2000:])
        run.stage_status.append({"stage": st, "status": status, "detail": detail})
        run.log(st, status, detail=detail, seconds=round(time.time() - t0, 2))
        print(f"[{status}] {st}: {detail}", flush=True)
    write_table(run.out / "stage_status.csv", "stage_status", run.stage_status)
    md = run.logf.with_suffix(".md")
    md.write_text("# Run summary\n\n" + "\n".join(f"- **{s['stage']}** {s['status']}: {s['detail']}" for s in run.stage_status) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
