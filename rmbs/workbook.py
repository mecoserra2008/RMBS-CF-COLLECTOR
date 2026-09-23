"""Refresh RMBS_CF_Valuation_Model.xlsx from the pipeline outputs, keeping every formula; recalculate with
LibreOffice and assert zero formula errors.

    python -m rmbs.workbook [--config config/run.yaml] [--no-recalc]

History: all rows of every out/<ISIN>_<ticker>.csv in SCHEMA order (A..AW), then the accounting checks, realised CPR
and Bloomberg paste/delta columns (AX..BI). Deals: one row per ISIN from config/deals + the last filed IPD; extra
columns AD.. carry the prospectus terms (IPD rule, step-ups, floor, triggers, call, legal final, shortfall option) and
the pipeline status. Inputs!G20 + Engine!BE:BF implement the principal-shortfall option (Hipocat 11: paid < due,
unpaid principal carried to the next IPD).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter as L

from . import calendar as cal
from . import config as C
from . import history as Hs
from .schema import SCHEMA
from .sources import REGISTRY

WB = "RMBS_CF_Valuation_Model.xlsx"
HIST_MAX = 2003                      # formulas in Inputs cover History rows 4..2003
NS = len(SCHEMA)                     # 49 -> checks start at column 50 (AX)
BLUE = Font(color="1F4E9E")
GREEN = Font(color="1E7B34")
YELLOW = PatternFill("solid", fgColor="FFF2CC")


def col(i: int) -> str:
    return L(i)


# column letters of SCHEMA fields in History
SC = {k: col(i + 1) for i, k in enumerate(SCHEMA)}
CHECKS = ["chk: beg−prin−end", "chk: factor", "chk: per-note×n − prin", "chk: coupon − (index+margin)",
          "chk: interest (rel.)", "realised CPR (prepay only)", "realised CPR (prepay+repurch.)",
          "A share of collateral principal", "BBG factor (paste)", "Δ factor (issuer − BBG)", "BBG principal (paste)",
          "Δ principal (issuer − BBG)", "chk: due − paid − shortfall"]
CK = {name: col(NS + 1 + i) for i, name in enumerate(CHECKS)}


def check_formulas(r: int) -> list[str]:
    s = lambda k: f"{SC[k]}{r}"
    bbg_f, bbg_p = f"{CK['BBG factor (paste)']}{r}", f"{CK['BBG principal (paste)']}{r}"
    return [f'=IF({s("beg_balance")}="","",{s("beg_balance")}-{s("principal_paid")}-{s("end_balance")})',
            f'=IF({s("pool_factor")}="","",{s("end_balance")}/{s("original_balance")}-{s("pool_factor")})',
            f'=IF({s("principal_per_note")}="","",{s("n_notes")}*{s("principal_per_note")}-{s("principal_paid")})',
            f'=IF({s("coupon_rate")}="","",{s("coupon_rate")}-({s("index_rate")}+{s("margin_bp")}/10000))',
            f'=IF(OR({s("interest_paid")}="",{s("interest_paid")}=0,{s("coupon_rate")}="",{s("accrual_days")}=""),"",'
            f'{s("beg_balance")}*{s("coupon_rate")}*{s("accrual_days")}/360/{s("interest_paid")}-1)',
            f'=IF({s("coll_prepayments")}="","",1-(1-{s("coll_prepayments")}/({s("coll_beg_balance")}-{s("coll_sched_principal")}))^4)',
            f'=IF({s("coll_prepayments")}="","",1-(1-({s("coll_prepayments")}+{s("coll_repurchases")})/({s("coll_beg_balance")}-{s("coll_sched_principal")}))^4)',
            f'=IF({s("coll_sched_principal")}="","",{s("principal_paid")}/({s("coll_sched_principal")}+{s("coll_prepayments")}+{s("coll_repurchases")}))',
            None,
            f'=IF({bbg_f}="","",{s("pool_factor")}-{bbg_f})',
            None,
            f'=IF({bbg_p}="","",{s("principal_paid")}-{bbg_p})',
            f'=IF({s("principal_due")}="","",{s("principal_due")}-{s("principal_paid")}-{s("principal_shortfall")})']


def _val(v):
    if v == "" or v is None:
        return None
    return v


def refresh_history(wb, rows_all: list[dict]):
    ws = wb["History"]
    ws.delete_rows(3, ws.max_row)
    ws["A1"] = ("Issuer-reported payment history — every row of out/<ISIN>_<ticker>.csv (schema = rmbs_harvester.SCHEMA, "
                "refreshed by `python -m rmbs.workbook`). Columns AX.. are checks and Bloomberg paste/delta columns.")
    hdr = SCHEMA + CHECKS
    for j, h in enumerate(hdr, start=1):
        c = ws.cell(3, j, h)
        c.font = Font(bold=True)
    for i, r in enumerate(rows_all):
        rr = 4 + i
        for j, k in enumerate(SCHEMA, start=1):
            v = _val(r.get(k))
            if k in ("payment_date", "accrual_start", "accrual_end") and v:
                v = dt.date.fromisoformat(str(v))
            ws.cell(rr, j, v)
        for j, f in enumerate(check_formulas(rr)):
            c = ws.cell(rr, NS + 1 + j, f)
            if CHECKS[j].startswith("BBG") and f is None:
                c.fill = YELLOW
        ws.cell(rr, SCHEMA.index("payment_date") + 1).number_format = "yyyy-mm-dd"
    ws.freeze_panes = "F4"


def deal_row(isin: str, deal: dict, rows: list[dict], existing: dict, status: dict) -> dict:
    s = REGISTRY[isin]
    last = rows[-1] if rows else None
    rules = C.ipd_rules(deal)
    out = dict(existing)
    out.update({"ISIN": isin, "Ticker": s.ticker.replace("_", " "), "Deal": s.deal,
                "Country": "PT" if isin.startswith("XS") else "ES", "Reporting entity": s.manager})
    if deal.get("legal_final"):
        out["Legal final"] = dt.date.fromisoformat(str(deal["legal_final"]))
    if deal.get("original_balance"):
        out["A original (€)"] = float(deal["original_balance"])
    if deal.get("clean_up_call_pct") is not None:
        out["Clean-up call (% of cut-off pool)"] = float(deal["clean_up_call_pct"])
    if deal.get("pool_at_closing"):
        out["Pool at cut-off (€)"] = float(deal["pool_at_closing"])
    if last:
        d = dt.date.fromisoformat(last["payment_date"])
        is_new = existing.get("Last IPD") is None or (isinstance(existing.get("Last IPD"), dt.datetime) and existing["Last IPD"].date() < d)
        out["Last IPD"] = d
        out["As-of IPD (last report)"] = d
        out["A current (€)"] = float(last["end_balance"]) if last["end_balance"] != "" else None
        if rules:                                          # the Engine adjusts (EDATE + WORKDAY): write the unadjusted date
            out["Next IPD (unadj.)"] = cal.next_ipds([dict(x, convention="none") for x in rules], d, 1)[0]
        from .engine import margin_at
        m = margin_at(deal, cal.next_ipds(rules, d, 1)[0]) if rules else deal.get("margin_bp")
        if m is not None:
            out["A margin"] = float(m) / 1e4
        if last.get("sub_end_balance") not in ("", None):
            out["Subordinate current (€)"] = float(last["sub_end_balance"])
        if last.get("coll_end_balance_net") not in ("", None):
            out["Pool current, net of deemed losses (€)"] = float(last["coll_end_balance_net"])
        if is_new and last.get("next_index_rate") not in ("", None):
            out["Current-period index fixing"] = float(last["next_index_rate"])
    miss = C.missing_fields(deal)
    out["Data status"] = (f"History {len(rows)} IPDs" + (f" to {rows[-1]['payment_date']}" if rows else "") +
                          f"; gate {status.get('gate', '')}; model {status.get('model', '')}; "
                          + ("config complete" if not miss else "CONFIG_INCOMPLETE: " + ",".join(miss))
                          + ("" if existing.get("WAC") not in (None, "") or not rows else "; pool/WAC/sched. principal not in the filings held"))
    # prospectus-term columns (AD..)
    ms = deal.get("margin_steps") or []
    out.update({"IPD rule": json.dumps(rules[-1] if rules else None),
                "Margin step-ups": "; ".join(f"{x.get('from')}: {x.get('margin_bp')}bp" for x in ms) or "none known",
                "Coupon floor": ("" if deal.get("coupon_floor") is None else f"{deal['coupon_floor']} from {deal.get('coupon_floor_from', 'start')}"
                                 + (f" to {deal['coupon_floor_to']}" if deal.get("coupon_floor_to") else "")),
                "Amortisation / triggers": json.dumps({"amortisation": deal.get("amortisation"), "triggers": deal.get("triggers")}, default=str)[:250],
                "Reserve": json.dumps(deal.get("reserve"), default=str)[:200],
                "Issuer-stated call": str(deal.get("call_issuer_stated") or ""),
                "Principal shortfall option": "YES (set Inputs!G20 = paid/due)" if deal.get("principal_shortfall_option") else "no",
                "Conflicts / unverified": "; ".join((deal.get("conflicts") or []) + (deal.get("unverified") or []))[:300]})
    return out


EXTRA_COLS = ["IPD rule", "Margin step-ups", "Coupon floor", "Amortisation / triggers", "Reserve", "Issuer-stated call",
              "Principal shortfall option", "Conflicts / unverified"]


def refresh_deals(wb, root: Path, statuses: dict):
    ws = wb["Deals"]
    hdr = [c.value for c in ws[1]][:29]
    for j, h in enumerate(EXTRA_COLS, start=30):
        ws.cell(1, j, h).font = Font(bold=True)
    hdr_all = hdr + EXTRA_COLS
    rows_by = {ws.cell(r, 1).value: r for r in range(2, 19)}
    for isin in REGISTRY:
        r = rows_by.get(isin)
        existing = {hdr_all[j - 1]: ws.cell(r, j).value for j in range(1, len(hdr_all) + 1)}
        deal = C.load_deal(isin, root / "config" / "deals")
        rows = Hs.load(root / "out", isin)
        new = deal_row(isin, deal, rows, existing, statuses.get(isin, {}))
        for j, h in enumerate(hdr_all, start=1):
            v = new.get(h)
            ws.cell(r, j, v)
            if isinstance(v, (dt.date, dt.datetime)):
                ws.cell(r, j).number_format = "yyyy-mm-dd"


def refresh_sources(wb, root: Path):
    ws = wb["Sources"]
    ws.delete_rows(2, ws.max_row)
    with open(root / "filing_locations.csv", newline="", encoding="utf-8") as h:
        for i, r in enumerate(csv.DictReader(h), start=2):
            for j, k in enumerate(["isin", "ticker", "deal", "doc_type", "period", "url", "status", "where_principal_is"], 1):
                ws.cell(i, j, r[k])


def add_shortfall_option(wb):
    ins, eng = wb["Inputs"], wb["Engine"]
    ins["F20"] = "Class A principal paid / due (1 = no shortfall; Hipocat 11 option)"
    if ins["G20"].value in (None, ""):
        ins["G20"] = 1
    ins["G20"].fill = YELLOW
    ins["G20"].font = BLUE
    eng["BE9"], eng["BF9"] = "Prin. A due", "A shortfall c/f"
    eng["BF10"] = 0
    for r in range(11, 191):
        eng[f"BE{r}"] = f"=IF(AB{r},O{r},IF(D{r},MIN(O{r},AC{r}*X{r}+BF{r - 1}),0))"
        eng[f"AD{r}"] = f"=IF(AB{r},O{r},IF(D{r},MIN(O{r},IF(E{r},AC{r}*X{r}+BF{r - 1},BE{r}*Inputs!$G$20)),0))"
        eng[f"AE{r}"] = f"=IF(AB{r},P{r},IF(D{r},MAX(MIN(P{r},X{r}-MIN(O{r},AC{r}*X{r})),0),0))"
        eng[f"BF{r}"] = f"=IF(OR(AB{r},E{r}),0,BE{r}-AD{r})"


def fix_inputs_refs(wb):
    ins = wb["Inputs"]
    cpr_col = CK["realised CPR (prepay only)"]
    st_col = SC["parse_status"]
    n = HIST_MAX
    ins["G37"] = f"=COUNTIF(History!A4:A{n},$C$3)"
    ins["G38"] = f"=_xlfn.MAXIFS(History!E4:E{n},History!A4:A{n},$C$3)"
    ins["G39"] = (f'=IFERROR(AVERAGEIFS(History!{cpr_col}4:{cpr_col}{n},History!A4:A{n},$C$3,History!E4:E{n},'
                  f'">"&EDATE(G38,-12)),"n/a")')
    ins["G40"] = f'=SUMPRODUCT((History!A4:A{n}=$C$3)*(LEFT(History!{st_col}4:{st_col}{n},6)="review"))'
    ins["F40"] = "Rows not 'ok' after validation (count)"
    ins["G41"] = '=IF(G38=C7,"consistent","CHECK: Deals row older/newer than History")'


def update_readme(wb, n_rows: int, vdate: str):
    ws = wb["README"]
    for r in range(20, 30):
        ws.cell(r, 1).value = None
    lines = ["Current state (refreshed by python -m rmbs.workbook)",
             f"History: {n_rows} issuer-reported IPD rows (all ISINs); Deals: all 17 ISINs from config/deals/*.yaml "
             f"(prospectus terms; null = not yet read -> CONFIG_INCOMPLETE) + the last filed IPD. Valuation date {vdate}.",
             "Inputs!G20 = Class A principal paid/due (principal-shortfall option, Hipocat 11: 2019 paid/due 0.03-0.09); "
             "unpaid principal is carried in Engine!BF and swept at the last live IPD.",
             "Clean price 98.50, the flat 3M curve and the current-period fixings are placeholders - replace with market inputs.",
             "Model acceptance per ISIN: out/champion_models.csv (NO_RELIABLE_MODEL = price only under an explicit scenario)."]
    for i, t in enumerate(lines):
        ws.cell(20 + i, 1, t)


def recalc(path: Path) -> Path:
    """LibreOffice headless round trip: loads (recomputes every formula) and writes cached values."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError("LibreOffice not installed")
    with tempfile.TemporaryDirectory() as td:
        prof = Path(td) / "profile"
        subprocess.run([exe, f"-env:UserInstallation=file://{prof}", "--headless", "--calc", "--convert-to",
                        "xlsx:Calc MS Excel 2007 XML", "--outdir", td, str(path)], check=True, capture_output=True, timeout=600)
        out = Path(td) / path.name
        shutil.copy(out, path.with_suffix(".recalc.xlsx"))
    return path.with_suffix(".recalc.xlsx")


ERRORS = ("#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!", "Err:")


def scan_errors(path: Path) -> list[str]:
    wb = openpyxl.load_workbook(path, data_only=True)
    bad = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith(ERRORS):
                    bad.append(f"{ws.title}!{c.coordinate}={c.value}")
    return bad


def count_formulas(path: Path) -> int:
    wb = openpyxl.load_workbook(path)
    return sum(1 for ws in wb.worksheets for row in ws.iter_rows() for c in row
               if isinstance(c.value, str) and c.value.startswith("="))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rmbs.workbook")
    ap.add_argument("--config", default="config/run.yaml")
    ap.add_argument("--no-recalc", action="store_true")
    a = ap.parse_args(argv)
    cfg = C.load_run(a.config)
    root = Path(cfg["root"])
    path = root / WB
    wb = openpyxl.load_workbook(path)
    rows_all = [r for i in REGISTRY for r in Hs.load(root / "out", i)]
    gate = {r["isin"]: r["gate_status"] for r in csv.DictReader(open(root / "out" / "reproduction_gate.csv"))} \
        if (root / "out" / "reproduction_gate.csv").exists() else {}
    champ = {r["isin"]: r["status"] for r in csv.DictReader(open(root / "out" / "champion_models.csv"))} \
        if (root / "out" / "champion_models.csv").exists() else {}
    statuses = {i: {"gate": gate.get(i, ""), "model": champ.get(i, "")} for i in REGISTRY}
    refresh_history(wb, rows_all)
    refresh_deals(wb, root, statuses)
    refresh_sources(wb, root)
    add_shortfall_option(wb)
    fix_inputs_refs(wb)
    update_readme(wb, len(rows_all), str(cfg["valuation_date"]))
    wb.save(path)
    n0 = count_formulas(path)
    print(f"workbook: {len(rows_all)} History rows, {n0} formulas")
    if a.no_recalc:
        return 0
    rc = recalc(path)
    errs = scan_errors(rc)
    n1 = count_formulas(rc)
    print(f"LibreOffice recalc: {n1} formulas kept, {len(errs)} formula errors")
    for e in errs[:20]:
        print("  ", e)
    if n1 < n0 or errs:
        return 1
    shutil.move(rc, path)            # the delivered workbook carries LibreOffice-computed cached values
    return 0


if __name__ == "__main__":
    sys.exit(main())
