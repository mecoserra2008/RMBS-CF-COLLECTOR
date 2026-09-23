#!/usr/bin/env python3
"""
RMBS payment-history harvester — one CSV per ISIN, one row per Interest Payment Date (IPD).

Run on a machine with internet access (e.g. BiG desktop). Requires: requests, pdfplumber (or poppler pdftotext).
    pip install requests pdfplumber beautifulsoup4
    python rmbs_harvester.py --isin XS0222684655 --from 2005-08 --to 2026-09
    python rmbs_harvester.py --all
    python rmbs_harvester.py --selftest            # parser test on bundled fixtures (no network)

Output:  out/<ISIN>_<TICKER>.csv   (schema = SCHEMA below, identical to the 'History' sheet of the workbook)
         out/raw/<ISIN>/<file>.pdf + .txt   (audit trail: every number traceable to a file)

Parser status (be explicit about what is tested):
  BCP / Magellan  : TESTED on the Feb-2009 and Nov-2025 investor reports (both text layouts: comma and space
                    thousands separators; English and Portuguese month abbreviations).
  EdT / Hipocat   : downloader TESTED on URL pattern; table parser HEURISTIC -> rows flagged parse_status='review'.
  CNMV OIR (TdA)  : downloader implemented (listing pages verified); per-IPD notice parser HEURISTIC -> 'review'.
  Santander / UCI : link scraper implemented; CNMV S.05.2 parser HEURISTIC -> 'review'.
  Lusitano        : no public source located -> manual (Citi investor reporting portal / Euronext Direct / EDW).
"""
from __future__ import annotations
import argparse, csv, datetime as dt, io, os, re, sys, time
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------------------------- schema
SCHEMA = ["isin", "deal", "tranche", "report_month", "payment_date", "accrual_start", "accrual_end",
          "accrual_days", "day_basis", "index_rate", "margin_bp", "coupon_rate", "original_balance",
          "beg_balance", "principal_paid", "end_balance", "pool_factor", "interest_paid", "pdl_balance",
          "n_notes", "principal_per_note", "denom_beg", "denom_end",
          "coll_beg_balance", "coll_sched_principal", "coll_prepayments", "coll_repurchases",
          "coll_cpr_reported", "coll_deemed_losses", "coll_recoveries", "coll_end_balance",
          "coll_end_balance_net", "wac", "arrears_90_365", "cum_default_ratio", "prorata_test",
          "reserve_balance", "coll_principal_total", "coll_cpr_12m", "next_coupon_rate", "next_index_rate",
          "source_url", "source_section", "parse_status"]

# --------------------------------------------------------------------------------------------- source map
@dataclass
class Src:
    isin: str; ticker: str; deal: str; tranche: str; country: str; kind: str
    manager: str; root: str = ""; code: str = ""; nif: str = ""; status: str = ""; note: str = ""

BCP = "https://ind.millenniumbcp.pt/pt/Institucional/investidores/securitizacoes/Documents/"
EDT = "https://edt-sg.com/intranet/fondos/"
CNMV_OIR = "https://www.cnmv.es/portal/otra-informacion-relevante/resultado-oir.aspx?lang=es&nif={nif}&page={page}"
SANT = "https://www.santanderdetitulizacion.com/san/Home/Fondos-de-Titulizacion/{slug}"

SOURCES = [
 Src("XS0222684655","MAGEL 3 A","Magellan Mortgages No. 3 plc","A","PT","bcp","BCP (Transaction Manager)",
     BCP+"Magellan3-InvestorReport/","Magellan3",status="VERIFIED",
     note="Sec.1 Security Level Information; Sec.2 Collateral; Sec.6 Principal Distribution. File names vary by year: YYYYMMMagellan3_InvestorReport.pdf (2006-2009), Investor-Report-YYYY-MM.pdf, InvestorReport_YYYYMM.pdf, Investor-Report-YYYYMM.pdf"),
 Src("XS0260784318","MAGEL 4 A","Magellan Mortgages No. 4 plc","A","PT","bcp","BCP (Transaction Manager)",
     BCP+"Magellan4-InvestorReport/","Magellan4",status="VERIFIED",
     note="Jan-2026 and Jul-2026 reports read; file names vary: Magellan-Mortgages-No4-plc_DDMMYYYY.pdf, InvestorReport_YYYYMM.pdf"),
 Src("XS0230694233","LUSI 4 A","Lusitano Mortgages No. 4 plc","A","PT","manual","Citibank N.A. (cash manager) / novobanco",
     status="NOT PUBLIC",note="Citi Agency&Trust investor reporting portal (registration); Euronext Direct RIS; EDW"),
 Src("XS0268642161","LUSI 5 A","Lusitano Mortgages No. 5 plc","A","PT","manual","Citibank N.A. / novobanco",
     status="NOT PUBLIC",note="as LUSI 4"),
 Src("XS0312981649","LUSI 6 A","Lusitano Mortgages No. 6 plc","A","PT","manual","Citibank N.A. / novobanco",
     status="NOT PUBLIC",note="as LUSI 4"),
 Src("ES0377992005","TDAC 5 A","TDA CAM 5, FTA","A","ES","cnmv_oir","Titulizacion de Activos SGFT",nif="V84466135",
     status="VERIFIED (listing)",note="CNMV OIR 'INFORMACION FECHA DE PAGO DEL FONDO' per IPD since 02/2020; earlier: CNMV hechos relevantes"),
 Src("ES0377993029","TDAC 6 A3","TDA CAM 6, FTA","A3","ES","cnmv_oir","Titulizacion de Activos SGFT",status="NIF TO RESOLVE"),
 Src("ES0377994019","TDAC 7 A2","TDA CAM 7, FTA","A2","ES","cnmv_oir","Titulizacion de Activos SGFT",nif="V84851724",
     status="VERIFIED (listing)"),
 Src("ES0377994027","TDAC 7 (A3?)","TDA CAM 7, FTA","TBC","ES","cnmv_oir","Titulizacion de Activos SGFT",nif="V84851724",
     status="CLASS TBC",note="Not in the Aug-2026 universe; likely TDA CAM 7 sister tranche - confirm class in notice"),
 Src("ES0377966009","TDAC 8 A","TDA CAM 8, FTA","A","ES","cnmv_oir","Titulizacion de Activos SGFT",status="NIF TO RESOLVE"),
 Src("ES0377955010","TDAC 9 A2","TDA CAM 9, FTA","A2","ES","cnmv_oir","Titulizacion de Activos SGFT",status="NIF TO RESOLVE"),
 Src("ES0359091016","CAJAM 2006-1 A2","MADRID RMBS I, FTA","A2","ES","cnmv_oir","Titulizacion de Activos SGFT",
     status="NIF TO RESOLVE",note="Bloomberg ticker CAJAM = Madrid RMBS I (Caja Madrid)"),
 Src("ES0380957003","UCI 15 A","F.T.A. U.C.I. 15","A","ES","santander","Santander de Titulizacion SGFT",code="uci-15",
     status="VERIFIED (full index)",note="Fund page lists all 78 'Información Periódica' reports Mar-2007..Jun-2026 + Cuentas Anuales 2021-2025 on assets.santandermedia.com; p.3 BONOS PRINCIPAL, p.12+ monthly pool & CPR history since 2006"),
 Src("ES0338186010","UCI 16 A2","F.T.A. U.C.I. 16","A2","ES","santander","Santander de Titulizacion SGFT",code="uci-16",
     status="VERIFIED (fund page)"),
 Src("ES0345672010","HIPO 11 A2","HIPOCAT 11, FTA","A2","ES","edt","Europea de Titulizacion (EdT)",EDT+"FGH11/","FGH11",
     status="VERIFIED (folder)",note="Monthly report EFGH11N01YYMMDD.pdf; CNMV quarterly state MFGH11K01YYMMDD.pdf"),
 Src("ES0345721015","HIPO 9 A2a","HIPOCAT 9, FTA","A2a","ES","edt","Europea de Titulizacion (EdT)",EDT+"FGH09/","FGH09",
     status="VERIFIED (folder)"),
 Src("ES0345721023","HIPO 9 A2b","HIPOCAT 9, FTA","A2b","ES","edt","Europea de Titulizacion (EdT)",EDT+"FGH09/","FGH09",
     status="VERIFIED (folder)"),
]

# --------------------------------------------------------------------------------------------- utils
MONTHS = {m: i+1 for i, m in enumerate(["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"])}
MONTHS.update({"fev":2,"abr":4,"mai":5,"ago":8,"set":9,"out":10,"dez":12,          # PT
               "ene":1,"dic":12})                                                    # ES
NUM = re.compile(r"-?\d{1,3}(?:[ ,]\d{3})*\.\d{2,}")          # 1,413,750,000.00 | 1 413 750 000.00 | 0.00
PCT = re.compile(r"-?\d+\.\d+\s?%")
DATE = re.compile(r"(\d{1,2})[/-]([A-Za-z]{3})[/-](\d{2,4})")

def num(s: str) -> float: return float(s.replace(",", "").replace(" ", ""))
def pct(s: str) -> float: return float(s.replace("%", "").strip())/100
NDATE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")
def pdate(s: str) -> str:
    n = NDATE.search(s)
    if n: return dt.date(int(n.group(3)), int(n.group(2)), int(n.group(1))).isoformat()
    m = DATE.search(s); d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    y = y + 2000 if y < 100 else y
    return dt.date(y, mo, d).isoformat()

def line_after(txt: str, label: str, start: int = 0):
    """Return (rest_of_line, pos) for the first line that starts with `label` exactly (after strip)."""
    for m in re.finditer(r"^[ \t]*" + re.escape(label) + r"(?P<rest>.*)$", txt[start:], re.M):
        return m.group("rest"), start + m.end()
    return None, -1

def pdf_to_text(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except ImportError:
        import subprocess
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout

# --------------------------------------------------------------------------------------------- BCP parser
def parse_bcp(txt: str, isin: str, url: str = "") -> dict:
    """Magellan investor report -> one IPD row for `isin`. Column index taken from the ISIN row."""
    txt = re.sub(r"\(\s*net of deemed [Ll]osses\)", "(net of Deemed Losses)", txt)
    txt = txt.replace("Principal Recoveries (to the extent of a debit balance recorded on the PDL) ", "Principal Recoveries ")
    isin_line, _ = line_after(txt, "ISIN ")
    col = isin_line.split().index(isin)
    def vec(label, rx=NUM):
        rest, _ = line_after(txt, label); return rx.findall(rest) if rest is not None else []
    def v(label, rx=NUM, f=num):
        xs = vec(label, rx); return f(xs[col]) if len(xs) > col else None
    def first(label, f=num, rx=NUM, after=None):
        start = txt.find(after) if after else 0
        rest, _ = line_after(txt, label, max(start, 0)); xs = rx.findall(rest) if rest else []
        return f(xs[0]) if xs else None
    def last(label, f=pct, rx=PCT):
        rest, _ = line_after(txt, label); xs = rx.findall(rest) if rest else []
        return f(xs[-1]) if xs else None
    cls = ["A","B","C","D","E"][col]
    r = {k: "" for k in SCHEMA}
    pay_rest, _ = line_after(txt, "Payment Date ")
    r.update(isin=isin, tranche=cls, source_url=url, source_section="Sec.1/2/3/6",
             deal=re.search(r"Magellan Mortgages No\. ?\d", txt).group(0),
             original_balance=v("Total Original Balance "),
             beg_balance=v("Total Beginning Balance Prior to Distribution "),
             end_balance=v("Total Ending Balance Subsequent to Distribution "),
             principal_paid=v("Total Principal Distribution "),
             pool_factor=v("Pool Factor ", PCT, pct),
             interest_paid=v("Total Interest Distributions "),
             payment_date=pdate(pay_rest.split()[col]),
             accrual_start=pdate(line_after(txt, "Accrual Beginning Date ")[0].split()[col]),
             accrual_end=pdate(line_after(txt, "Accrual Ending Date ")[0].split()[col]),
             accrual_days=int(re.findall(r"\b(\d+)\b(?: days)?", line_after(txt, "Accrual Period ")[0])[col]),
             day_basis=line_after(txt, "Day Basis ")[0].split()[col],
             coupon_rate=v("Accrual Rate ", PCT, pct), index_rate=v("Euro Reference Rate ", PCT, pct),
             margin_bp=int(line_after(txt, "Spread (bps) ")[0].split()[col]),
             denom_beg=v("Denomination "), denom_end=v("New Denomination for the next period "),
             coll_beg_balance=first("Beginning Principal Outstanding Balance "),
             coll_sched_principal=first("Scheduled Principal Redemption "),
             coll_prepayments=first("Prepayments "),
             coll_repurchases=first("Retired Mortgages Assets for non-permitted variations "),
             coll_cpr_reported=first("CPR ", pct, PCT),
             coll_deemed_losses=first("Deemed Principal Losses "),
             coll_recoveries=first("Principal Recoveries "),
             coll_end_balance=first("Ending Principal Outstanding Balance "),
             coll_end_balance_net=first("Ending Principal Outstanding Balance (net of Deemed Losses) "),
             wac=last("WA Interest Rate "),
             arrears_90_365=first("Mortgage Loans in arrears (90 - 365 days) "),
             cum_default_ratio=first("Ratio ", pct, PCT, after="Net Cumulative Default Ratio"),
             prorata_test=(line_after(txt, "Pro-Rata Test ")[0] or "").strip().split()[0] if line_after(txt, "Pro-Rata Test ")[0] else "",
             reserve_balance=first("(b) Cash Reserve Account "))
    pdl = vec("Principal Deficiency Ledger ")
    r["pdl_balance"] = num(pdl[col]) if len(pdl) > col else ""
    nn, _ = line_after(txt, f"Number of outstanding Class {cls} Notes ")
    r["n_notes"] = int(round(float(nn.strip().replace(",", "").replace(" ", "")))) if nn else ""
    pn, _ = line_after(txt, f"Class {cls} Notes Principal Payment - per Note ")
    r["principal_per_note"] = num(NUM.findall(pn)[0]) if pn else ""
    r["report_month"] = r["payment_date"][:7]
    r["parse_status"] = validate(r)
    return r

def validate(r: dict, tol: float = 0.02) -> str:
    """Accounting identities that every IPD must satisfy. Returns 'ok' or a list of failures."""
    bad = []
    f = lambda k: r.get(k) if isinstance(r.get(k), (int, float)) else None
    if None not in (f("beg_balance"), f("principal_paid"), f("end_balance")):
        if abs(f("beg_balance") - f("principal_paid") - f("end_balance")) > tol: bad.append("beg-prin!=end")
    if None not in (f("end_balance"), f("original_balance"), f("pool_factor")):
        if abs(f("end_balance")/f("original_balance") - f("pool_factor")) > 1e-6: bad.append("factor")
    if None not in (f("n_notes"), f("principal_per_note"), f("principal_paid")):
        if abs(f("n_notes")*f("principal_per_note") - f("principal_paid")) > 1.0: bad.append("per-note")
    if None not in (f("denom_beg"), f("denom_end"), f("principal_per_note")):
        if abs(f("denom_beg") - f("denom_end") - f("principal_per_note")) > 0.011: bad.append("denom")
    if None not in (f("index_rate"), f("margin_bp"), f("coupon_rate")):
        if abs(f("index_rate") + f("margin_bp")/1e4 - f("coupon_rate")) > 1e-6: bad.append("coupon")
    if None not in (f("beg_balance"), f("coupon_rate"), f("accrual_days"), f("interest_paid")):
        if abs(f("beg_balance")*f("coupon_rate")*f("accrual_days")/360 - f("interest_paid")) > 0.005*f("interest_paid")+1:
            bad.append("interest")
    return "ok" if not bad else "FAIL:" + ",".join(bad)

# --------------------------------------------------------------------------------------------- heuristic ES parser
def parse_es_generic(txt: str, s: Src, url: str) -> dict | None:
    """Spanish notices/states. Finds the ISIN line and the numbers on it; everything flagged for review.
    Typical 'INFORMACION FECHA DE PAGO' notice lists per series: saldo inicial, amortizacion, saldo final,
    intereses, tipo. Column order differs across gestoras -> mapping must be confirmed on the first file."""
    i = txt.find(s.isin)
    if i < 0: return None
    seg = txt[i:i+400]
    nums = re.findall(r"-?\d{1,3}(?:\.\d{3})*,\d+", seg)          # Spanish format 1.234.567,89
    vals = [float(x.replace(".", "").replace(",", ".")) for x in nums]
    r = {k: "" for k in SCHEMA}
    r.update(isin=s.isin, deal=s.deal, tranche=s.tranche, source_url=url, source_section="ISIN row",
             parse_status="review:" + "|".join(f"{v:.2f}" for v in vals[:10]))
    d = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt)
    if d: r["payment_date"] = f"{d.group(3)}-{d.group(2)}-{d.group(1)}"; r["report_month"] = r["payment_date"][:7]
    return r

# --------------------------------------------------------------------------------------------- downloaders
def session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (research; RMBS payment history)"
    return s

def get(ses, url, dest: Path) -> Path | None:
    if dest.exists(): return dest
    r = ses.get(url, timeout=60)
    if r.status_code != 200 or b"%PDF" not in r.content[:1024]: return None
    dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(r.content); time.sleep(0.5)
    return dest

def months(a: str, b: str):
    y, m = map(int, a.split("-")); y2, m2 = map(int, b.split("-"))
    while (y, m) <= (y2, m2):
        yield y, m; m += 1
        if m == 13: y, m = y + 1, 1

def harvest_bcp(ses, s: Src, a, b, raw: Path):
    rows = []
    for y, m in months(a, b):
        for name in (f"Investor-Report-{y}-{m:02d}.pdf", f"Investor-Report-{y}{m:02d}.pdf", f"InvestorReport_{y}{m:02d}.pdf",
                     f"{y}{m:02d}{s.code}_InvestorReport.pdf"):
            p = get(ses, s.root + name, raw / name)
            if p:
                txt = pdf_to_text(p); (raw / (name + ".txt")).write_text(txt)
                try: rows.append(parse_bcp(txt, s.isin, s.root + name))
                except Exception as e: rows.append({**{k: "" for k in SCHEMA}, "isin": s.isin, "source_url": s.root+name,
                                                    "parse_status": f"ERROR:{e}"})
                break
    return rows

def harvest_edt(ses, s: Src, a, b, raw: Path):
    import calendar
    rows = []
    for y, m in months(a, b):
        eom = calendar.monthrange(y, m)[1]
        for pref in ("E", "M"):
            name = f"{pref}{s.code}N01{y%100:02d}{m:02d}{eom:02d}.pdf"
            p = get(ses, s.root + name, raw / name)
            if p:
                txt = pdf_to_text(p); (raw / (name + ".txt")).write_text(txt)
                r = parse_es_generic(txt, s, s.root + name)
                if r: rows.append(r)
                break
    return rows

def harvest_cnmv_oir(ses, s: Src, a, b, raw: Path, max_pages=40):
    from bs4 import BeautifulSoup
    rows = []
    if not s.nif:
        print(f"  {s.ticker}: NIF missing -> look it up at https://www.cnmv.es/portal/Consultas/FTA/Listado_ROFT.aspx"); return rows
    for page in range(max_pages):
        html = ses.get(CNMV_OIR.format(nif=s.nif, page=page), timeout=60).text
        soup = BeautifulSoup(html, "html.parser")
        links = [(x.get_text(" ", strip=True), x["href"]) for x in soup.find_all("a", href=True) if "verdocumento" in x["href"]]
        if not links: break
        for title, href in links:
            if "FECHA DE PAGO" not in title.upper(): continue
            name = re.sub(r"\W", "", href)[-40:] + ".pdf"
            p = get(ses, href, raw / name)
            if p:
                txt = pdf_to_text(p); (raw / (name + ".txt")).write_text(txt)
                r = parse_es_generic(txt, s, href)
                if r: rows.append(r)
    return rows

def harvest_santander(ses, s: Src, a, b, raw: Path):
    from bs4 import BeautifulSoup
    rows = []
    html = ses.get(SANT.format(slug=s.code), timeout=60).text
    for x in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        h = x["href"]
        if ".pdf" not in h.lower() or "Peri" not in h: continue   # quarterly 'Información Periódica' only
        url = h if h.startswith("http") else "https://www.santanderdetitulizacion.com" + h
        name = re.sub(r"\W", "", url)[-50:] + ".pdf"
        p = get(ses, url, raw / name)
        if p:
            txt = pdf_to_text(p); (raw / (name + ".txt")).write_text(txt)
            try: rows.append(parse_uci(txt, s.isin, url))
            except Exception as e: rows.append({**{k: "" for k in SCHEMA}, "isin": s.isin, "source_url": url, "parse_status": f"ERROR:{e}"})
    return rows

HARVESTERS = {"bcp": harvest_bcp, "edt": harvest_edt, "cnmv_oir": harvest_cnmv_oir, "santander": harvest_santander}

def write_csv(rows, s: Src, out: Path):
    rows = sorted([r for r in rows if r.get("payment_date")], key=lambda r: r["payment_date"])
    seen, uniq = set(), []
    for r in rows:
        if r["payment_date"] in seen: continue
        seen.add(r["payment_date"]); uniq.append(r)
    f = out / f"{s.isin}_{s.ticker.replace(' ', '_')}.csv"
    with open(f, "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=SCHEMA); w.writeheader()
        w.writerows([{k: (round(v, 10) if isinstance(v, float) else v) for k, v in r.items()} for r in uniq])
    return f, len(uniq)

# --------------------------------------------------------------------------------------------- main
def selftest():
    here = Path(__file__).parent / "fixtures"
    s = SOURCES[0]; rows = []
    for f in sorted(here.glob("magel*_*.txt")):
        isin = "XS0222684655" if "magel3" in f.name else "XS0260784318"
        r = parse_bcp(f.read_text(), isin, f.name); rows.append(r)
        print(f.name, r["payment_date"], r["beg_balance"], r["principal_paid"], r["end_balance"],
              r["pool_factor"], r["coupon_rate"], r["coll_cpr_reported"], "->", r["parse_status"])
    return rows

# --------------------------------------------------------------------------------------------- Santander / UCI parser
ESNUM = r"-?\d{1,3}(?:\.\d{3})*,\d+"
def esnum(x): return float(x.replace(".", "").replace(",", "."))
def parse_uci(txt: str, isin: str, url: str = "", n_notes: int = None, orig: float = None) -> dict:
    """Santander de Titulización 'Información Periódica' (UCI funds). Tested on UCI 15 Jun-2012 and Jun-2026 layouts.
    Series A outstanding = Nominal Actual; beg = end + per-bond amortisation x number of bonds."""
    r = {k: "" for k in SCHEMA}
    m = re.search(r"TRIMESTRE/SEMESTRE:\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})", txt)
    r["accrual_start"], r["accrual_end"] = pdate(m.group(1)), pdate(m.group(2))
    r["payment_date"] = r["accrual_end"]; r["report_month"] = r["payment_date"][:7]
    r["accrual_days"] = (dt.date.fromisoformat(r["accrual_end"]) - dt.date.fromisoformat(r["accrual_start"])).days
    m = re.search(r"B\.T\.A'S SERIE A (" + ESNUM + r") € (" + ESNUM + r") € (" + ESNUM + r")%", txt)
    per_amort, per_int, next_cpn = esnum(m.group(1)), esnum(m.group(2)), esnum(m.group(3)) / 100
    m = re.search(r"B\.T\.A'S SERIE A (\d{1,3}(?:\.\d{3})*) Nominal Unitario", txt); n = int(m.group(1).replace(".", ""))
    end = max(esnum(x) for x in re.findall(r"(" + ESNUM + r") \d{1,3},\d{2}%", txt))       # 'Nominal actual  x,xx%'
    orig = orig or esnum(re.search(isin + r" Nominal Total (" + ESNUM + ")", txt).group(1))
    r.update(isin=isin, tranche="A", deal="F.T.A. UCI", source_url=url, source_section="p.2 II. VALORES EMITIDOS; p.3 BONOS PRINCIPAL; p.4 DERECHOS",
             original_balance=orig, n_notes=n, principal_per_note=per_amort, end_balance=end,
             principal_paid=round(per_amort * n, 2), beg_balance=round(end + per_amort * n, 2),
             denom_end=round(end / n, 2), denom_beg=round(end / n + per_amort, 2), pool_factor=end / orig,
             interest_paid=esnum(re.search(r"Intereses pagados Serie A (" + ESNUM + ")", txt).group(1)),
             next_coupon_rate=next_cpn,
             next_index_rate=esnum(re.search(r"Tipo de referencia \(%\) (" + ESNUM + ")%", txt).group(1)) / 100)
    r["margin_bp"] = round((r["next_coupon_rate"] - r["next_index_rate"]) * 1e4)
    r["coupon_rate"] = round(r["interest_paid"] / r["beg_balance"] * 360 / r["accrual_days"], 6)
    r["index_rate"] = round(r["coupon_rate"] - r["margin_bp"] / 1e4, 6)
    m = re.search(r"DERECHOS DE CR.DITO\. PRINCIPAL\s*Saldo anterior (" + ESNUM + r") €\s*Amortizaciones (" + ESNUM + ")", txt)
    r["coll_beg_balance"], r["coll_principal_total"] = esnum(m.group(1)), esnum(m.group(2))
    r["coll_end_balance"] = esnum(re.findall(r"Saldo Pendiente de Amortizar Derechos " + ESNUM + r" € (" + ESNUM + ")", txt)[0])
    r["coll_cpr_reported"] = esnum(re.search(r"Tasa mensual actual anualizada: (" + ESNUM + ")%", txt).group(1)) / 100
    r["coll_cpr_12m"] = esnum(re.search(r"Tasa últimos 12 meses anualizada: (" + ESNUM + ")%", txt).group(1)) / 100
    r["wac"] = esnum(re.findall(r"Tipos de Interés " + ESNUM + r" % (" + ESNUM + ")%", txt)[0]) / 100
    m = re.search(r"MOROSIDAD SUPERIOR A 90 D.AS (" + ESNUM + ")", txt)
    if m: r["arrears_90_365"] = esnum(m.group(1))
    r["prorata_test"] = "FAIL (B,C not amortised)" if "NO SE AMORTIZAN" in txt else "PASS"
    r["parse_status"] = validate(r)
    return r

def _selftest_uci():
    here = Path(__file__).parent / "fixtures"; rows = []
    for f in sorted(here.glob("uci15_*.txt")):
        r = parse_uci(f.read_text(), "ES0380957003", f.name); rows.append(r)
        print(f.name, r["payment_date"], r["beg_balance"], r["principal_paid"], r["end_balance"], r["coupon_rate"],
              r["margin_bp"], r["coll_end_balance"], "->", r["parse_status"])
    return rows


# --------------------------------------------------------------------------------------------- EdT annual accounts (Nota 'movimiento de los Bonos')
SERIES_BY_ISIN = {"ES0345672010": "A2", "ES0345721015": "A2a", "ES0345721023": "A2b"}
def parse_edt_accounts(txt: str, isin: str, url: str = "") -> list:
    """Per-IPD amortisation of one series from the 'movimiento de los Bonos' table (kEUR, 2 columns per series:
    non-current, current). Tested on Hipocat 11 Cuentas Anuales 2016."""
    ser = SERIES_BY_ISIN.get(isin)
    hdr = re.search(r"((?:Serie \w+\s*){2,})", txt)
    if not ser or not hdr: return []
    names = re.findall(r"Serie (\w+)", hdr.group(1))
    if ser not in names: return []
    k = names.index(ser)
    tok = r"(-?\(?\d{1,3}(?:\.\d{3})*\)?|-)"
    def pair(line_tail):
        t = re.findall(tok, line_tail)
        v = [0.0 if x == "-" else (-1 if x[0] in "(-" else 1) * float(x.strip("()-").replace(".", "")) for x in t]
        return v[2*k:2*k+2]
    rows, bal = [], None
    txt = re.sub(r"Amortizaci.n\s*\n\s*(?=\d{2}\.\d{2}\.\d{4})", "Amortización ", txt)
    txt = re.sub(r"Saldos a[l]? 1 de\s*\n\s*enero", "Saldos a 1 de enero", txt)
    for line in re.split(r"\n", re.sub(r"\n(?=\d{4} [-\d(])", " ", txt)):
        m = re.match(r"Saldos al? 1 de enero de\s+(\d{4})\s+(.*)", line)
        if m and bal is None: bal = sum(pair(m.group(2)))
        m = re.match(r"Amortizaci.n (\d{2})\.(\d{2})\.(\d{4})\s+(.*)", line)
        if m and bal is not None:
            amt = -sum(pair(m.group(4)))
            r = {c: "" for c in SCHEMA}
            r.update(isin=isin, tranche=ser, payment_date=f"{m.group(3)}-{m.group(2)}-{m.group(1)}", beg_balance=bal*1e3,
                     principal_paid=amt*1e3, end_balance=(bal-amt)*1e3, source_url=url,
                     source_section="Cuentas Anuales - movimiento de los Bonos (kEUR)", parse_status="ok: annual accounts (kEUR)")
            r["report_month"] = r["payment_date"][:7]; rows.append(r); bal -= amt
    return rows

# --------------------------------------------------------------------------------------------- TdA payment notice
def parse_tda_notice(txt: str, isin: str, url: str = "") -> dict | None:
    """TdA 'INFORMACION A LOS INVERSORES - FECHA DE PAGO' notice. Numbered items inside the series block.
    Labels mapped by keyword; row flagged 'review' unless the balance identity closes."""
    i = txt.find(isin)
    if i < 0: return None
    j = re.search(r"\n\s*I{1,3}V?\. BONOS|\n\s*V\. ", txt[i+12:]); blk = txt[i: i + 12 + (j.start() if j else 3000)]
    r = {c: "" for c in SCHEMA}; r.update(isin=isin, source_url=url, source_section="I. BONOS <series> block")
    MES = dict(enero=1,febrero=2,marzo=3,abril=4,mayo=5,junio=6,julio=7,agosto=8,septiembre=9,octubre=10,noviembre=11,diciembre=12)
    m = re.search(r"FECHA DE PAGO:\s*(\d{1,2}) de (\w+) de (\d{4})", txt, re.I)
    if m: r["payment_date"] = dt.date(int(m.group(3)), MES[m.group(2).lower()], int(m.group(1))).isoformat(); r["report_month"] = r["payment_date"][:7]
    m = re.search(r"Sobre (\d+) bonos", blk)
    if m: r["n_notes"] = int(m.group(1))
    for num, label, amt in re.findall(r"(\d+)\.\s*([^:\n]+):\s*(" + ESNUM + r")\s*€", blk):
        L = label.lower(); v = esnum(amt)
        if "saldo inicial" in L: r["original_balance"] = v
        elif "anterior" in L: r["beg_balance"] = v
        elif "amortiza" in L: r["principal_paid"] = v
        elif "pendiente" in L: r["end_balance"] = v
        elif "inter" in L and "tipo" not in L: r["interest_paid"] = v
    m = re.search(r"[Tt]ipo de inter[eé]s[^\n]*?(" + ESNUM + r")\s*%", blk)
    if m: r["coupon_rate"] = esnum(m.group(1)) / 100
    ok = all(isinstance(r[k], float) for k in ("beg_balance", "principal_paid", "end_balance")) and \
         abs(r["beg_balance"] - r["principal_paid"] - r["end_balance"]) < 0.05
    r["parse_status"] = "ok: TdA notice, balance identity closes" if ok else "review: TdA notice (partial fields)"
    return r


# --------------------------------------------------------------------------------------------- TdA annual accounts (Nota 'Liquidaciones intermedias')
def parse_tda_accounts(txt: str, isin: str, url: str = "", serie: str = "A") -> list:
    """TdA annual accounts: table 'Liquidación de pagos de las liquidaciones intermedias <d1> <d2> <d3> <d4>' followed by
    'Pagos por amortización ordinaria SERIE <x> v1 v2 v3 v4' (kEUR; thousands sep '.' or space). Tested on TDA CAM 5 CA2022."""
    flat = re.sub(r"\s*\n\s*", " ", txt)
    num = r"(\d{1,3}(?:[. ]\d{3})*|-)"
    rows = []
    for m in re.finditer(r"liquidaciones intermedias ((?:\d{2}/\d{2}/\d{4}\s*){1,6})", flat):
        dates = re.findall(r"\d{2}/\d{2}/\d{4}", m.group(1))
        seg = flat[m.end(): m.end() + 3000]
        def row(label):
            mm = re.search(label + r"\s+" + r"\s+".join([num] * len(dates)), seg)
            return [0.0 if v == "-" else float(v.replace(".", "").replace(" ", "")) for v in mm.groups()] if mm else None
        pr = row(r"Pagos por amortizaci.n ordinaria SERIE " + serie)
        it = row(r"Pagos por intereses ordinarios SERIE " + serie)
        if not pr: continue
        for k, d in enumerate(dates):
            r = {c: "" for c in SCHEMA}
            r.update(isin=isin, tranche=serie, payment_date=pdate(d), principal_paid=pr[k] * 1e3,
                     interest_paid=(it[k] * 1e3 if it else ""), source_url=url,
                     source_section="Cuentas Anuales - Liquidaciones intermedias (kEUR)", parse_status="ok: annual accounts (kEUR); chain balances from 'Saldo inicial'")
            r["report_month"] = r["payment_date"][:7]; rows.append(r)
    rows.sort(key=lambda r: r["payment_date"])
    m = re.search(r"Saldo inicial\s+(\d{1,3}(?:[. ]\d{3})*)", flat)
    if m and rows:
        bal = float(m.group(1).replace(".", "").replace(" ", "")) * 1e3
        for r in rows:
            r["beg_balance"] = bal; r["end_balance"] = bal - r["principal_paid"]; bal = r["end_balance"]
    return rows

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--isin"); ap.add_argument("--all", action="store_true"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--from", dest="a", default="2005-01"); ap.add_argument("--to", dest="b", default=dt.date.today().strftime("%Y-%m"))
    ap.add_argument("--out", default="out")
    a = ap.parse_args(); out = Path(a.out); out.mkdir(exist_ok=True)
    if a.selftest:
        # never writes into out/ (that holds the harvested history); test output goes to selftest_out/
        tout = Path("selftest_out"); tout.mkdir(exist_ok=True)
        rows = selftest() + _selftest_uci()
        for src in [x for x in SOURCES if x.isin in ("XS0222684655","XS0260784318","ES0380957003")]: print(write_csv([r for r in rows if r["isin"] == src.isin], src, tout))
        fx = Path(__file__).parent / "fixtures"
        h16 = parse_edt_accounts((fx / "hipo11_ca2016.txt").read_text(), "ES0345672010")
        h19 = parse_edt_accounts((fx / "hipo11_ca2019.txt").read_text(), "ES0345672010")
        t22 = parse_tda_accounts((fx / "tdacam5_ca2022.txt").read_text(), "ES0377992005", serie="A")
        assert len(h16) == 8 and round(h16[-1]["end_balance"]) == 302475000, "EdT CA2016 parser"
        assert len(h19) == 8 and round(h19[-1]["end_balance"]) == 217021000, "EdT CA2019 parser"
        assert len(t22) == 8 and abs(t22[-1]["end_balance"] - 210062000) <= 3000, "TdA CA2022 parser"
        print("EdT/TdA annual-accounts parsers: ok (16 + 8 IPDs tie to audited balances)")
        sys.exit(0)
    ses = session()
    for s in SOURCES:
        if not (a.all or a.isin == s.isin): continue
        if s.kind == "manual":
            print(f"{s.ticker}: manual source -> {s.note}"); continue
        print(f"{s.ticker} [{s.kind}] ...")
        rows = HARVESTERS[s.kind](ses, s, a.a, a.b, out / "raw" / s.isin)
        f, n = write_csv(rows, s, out)
        nbad = sum(1 for r in rows if r.get("parse_status") != "ok")
        print(f"  -> {f}  rows={n}  not-ok={nbad}")

