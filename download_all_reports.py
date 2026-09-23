#!/usr/bin/env python3
"""
Download EVERY historical payment report for the 17 RMBS, zip the PDFs, and parse them into one CSV per ISIN.

    python download_all_reports.py                 # everything
    python download_all_reports.py --only MAGEL    # one programme (MAGEL | UCI | HIPO | TDA | LUSI)
    python download_all_reports.py --no-parse      # download + zip only

Output
    reports/<ISIN>_<ticker>/<file>.pdf            every PDF found (never re-downloaded)
    RMBS_reports_ALL.zip                          all PDFs
    csv/<ISIN>_<ticker>.csv                       one row per IPD (parsed; schema = rmbs_harvester.SCHEMA)
    download_log.csv                              every URL tried: status, bytes, sha256
Dependencies: Python 3.9+, requests, beautifulsoup4, pdfplumber (pip install requests beautifulsoup4 pdfplumber).
Runs on the BiG desktop / any machine with normal internet. Polite: 0.4 s between requests, resumable.

How each programme is covered (why this finds the full history)
  Magellan 3/4 (BCP)   quarterly reports since 1st IPD; BCP renamed files over time, so every naming variant
                       seen in the filings is tried for every IPD month (verified variants listed in NAMES).
  UCI 15 / UCI 16      the fund page lists every 'Información Periódica' since 2007 + annual accounts; scraped
                       (UCI 15: the 83 verified links are also hard-coded as a fallback).
  Hipocat 9 / 11 (EdT) EdT stores every document under /intranet/fondos/<code>/ with a fixed naming rule
                       <E|M><code><doc><seq><YYMMDD>.pdf ; monthly (N01), CNMV quarterly (K01), annual (C01)
                       are tried for every month-end since closing.
  TDA CAM 5-9, Madrid RMBS I (TdA)
                       CNMV 'Otra información relevante' per fund NIF (payment notices 'FECHA DE PAGO' since
                       02/2020) + CNMV 'Hechos relevantes' archive (payment notices before 02/2020) + CNMV
                       periodic states. NIFs not yet known are resolved from the CNMV securitisation register.
  Lusitano 4/5/6       NOT public. Place the Citi investor-report PDFs in reports/<ISIN>_LUSI_x/ manually;
                       they are zipped and parsed like the others.
"""
from __future__ import annotations
import argparse, calendar, csv, datetime as dt, hashlib, re, sys, time, zipfile
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install requests beautifulsoup4 pdfplumber")

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import rmbs_harvester as H          # parsers + schema (tested)

TODAY = dt.date.today()
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research"
LOG = []

def fetch_pdf(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1000:
        return True
    try:
        r = S.get(url, timeout=60)
    except Exception as e:
        LOG.append((url, "ERR", 0, str(e)[:80])); return False
    time.sleep(0.4)
    ok = r.status_code == 200 and r.content[:5] == b"%PDF-"
    LOG.append((url, r.status_code, len(r.content), hashlib.sha256(r.content).hexdigest() if ok else ""))
    if ok:
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(r.content)
    return ok

def html(url: str) -> BeautifulSoup | None:
    try:
        r = S.get(url, timeout=60); time.sleep(0.4)
        LOG.append((url, r.status_code, len(r.content), "html"))
        return BeautifulSoup(r.text, "html.parser") if r.status_code == 200 else None
    except Exception as e:
        LOG.append((url, "ERR", 0, str(e)[:80])); return None

def months(y0, m0, step=1):
    y, m = y0, m0
    while dt.date(y, m, 1) <= TODAY:
        yield y, m
        m += step
        while m > 12: m -= 12; y += 1

# ------------------------------------------------------------------ BCP / Magellan
BCP = "https://ind.millenniumbcp.pt/{lang}/Institucional/investidores/securitizacoes/Documents/{folder}/"
MAGELLAN = {  # isin: (ticker, folder, code, first IPD year, first IPD month, IPD months step)
    "XS0222684655": ("MAGEL_3_A", "Magellan3-InvestorReport", "Magellan3", 2005, 8, 3),   # Feb/May/Aug/Nov, 15th
    "XS0260784318": ("MAGEL_4_A", "Magellan4-InvestorReport", "Magellan4", 2006, 10, 3),  # Jan/Apr/Jul/Oct, 20th
}
def bcp_names(code, y, m):
    n = code[-1]
    yield f"{y}{m:02d}{code}_InvestorReport.pdf"            # verified 2006-2023 style
    yield f"Investor-Report-{y}-{m:02d}.pdf"                 # verified Nov-2025
    yield f"Investor-Report-{y}{m:02d}.pdf"                  # verified Aug-2026
    yield f"InvestorReport_{y}{m:02d}.pdf"                   # verified May-2026, Jul-2026
    yield f"InvestorReport{y}{m:02d}.pdf"
    yield f"{y}{m:02d}_{code}_InvestorReport.pdf"            # verified Jan-2025 (Magellan 4)
    yield f"{code}_InvestorReport_{y}{m:02d}.pdf"            # verified Nov-2023
    yield f"InvestorReport_{y}-{m:02d}.pdf"                  # verified May-2023
    yield f"Investor_Report_{y}_{m:02d}.pdf"                 # verified Feb/Aug-2019, Jan-2019 (M4)
    for d in range(10, 21):
        yield f"Investor_Report_{d:02d}{m:02d}{y}.pdf"       # verified 16052019, 15022021
        yield f"Investor_Report_{y}_{m:02d}_{d:02d}{m:02d}{y}.pdf"   # verified 2020_11_12112020
    for d in range(10, 21):                                  # verified Jan-2026: Magellan-Mortgages-No4-plc_16012026.pdf
        yield f"Magellan-Mortgages-No{n}-plc_{d:02d}{m:02d}{y}.pdf"

def get_magellan(isin, out):
    tk, folder, code, y0, m0, step = MAGELLAN[isin]
    got = 0
    for y, m in months(y0, m0, step):
        for lang in ("pt", "en"):
            base = BCP.format(lang=lang, folder=folder)
            if any(fetch_pdf(base + nm, out / f"{y}-{m:02d}_{nm}") for nm in bcp_names(code, y, m)):
                got += 1; break
        else:
            print(f"   {tk}: no report found for {y}-{m:02d} (check BCP page manually)")
    return got

# ------------------------------------------------------------------ Santander / UCI
SANT_PAGES = {
    "ES0380957003": ["https://www.santanderdetitulizacion.com/san/Home/Fondos-de-Titulizacion/uci-15",
                     "https://www.santanderdetitulizacion.com/en/san/Home/Fondos-de-Titulizacion/uci-15/"],
    "ES0338186010": ["https://www.santanderdetitulizacion.com/san/Home/Fondos-de-Titulizacion/uci-16",
                     "https://www.santanderdetitulizacion.com/san/Home/Fondos-de-Titulizacion/uci-16/",
                     "https://www.santanderdetitulizacion.com/san/Home/Fondos-de-Titulizacion/uci+16/uci+16",
                     "https://www.santanderdetitulizacion.com/en/san/Home/Fondos-de-Titulizacion/uci-16/"],
}
def get_santander(isin, out):
    urls = set()
    for p in SANT_PAGES[isin]:
        soup = html(p)
        if soup:
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if ".pdf" in h.lower(): urls.add(urljoin(p, h))
    manifest = HERE / "filing_locations.csv"
    if manifest.exists():                                   # verified fallback list (UCI 15: 83 links)
        for r in csv.DictReader(open(manifest, encoding="utf-8")):
            if r["isin"] == isin and r["url"].lower().endswith(".pdf"): urls.add(r["url"])
    got = 0
    for u in sorted(urls):
        name = re.sub(r"[^\w.\-]", "_", u.split("/")[-1])[:120]
        got += fetch_pdf(u, out / name)
    return got

# ------------------------------------------------------------------ EdT / Hipocat
EDT = "https://edt-sg.com/intranet/fondos/{code}/"
HIPO = {"ES0345672010": ("HIPO_11_A2", "FGH11", 2007, 3),
        "ES0345721015": ("HIPO_9_A2a", "FGH09", 2005, 7),
        "ES0345721023": ("HIPO_9_A2b", "FGH09", 2005, 7)}
def get_edt(isin, out):
    tk, code, y0, m0 = HIPO[isin]; base = EDT.format(code=code); got = 0
    for y, m in months(y0, m0):
        eom = calendar.monthrange(y, m)[1]; stamp = f"{y%100:02d}{m:02d}{eom:02d}"
        for nm in (f"E{code}N01{stamp}.pdf", f"M{code}N01{stamp}.pdf",          # monthly report (verified naming)
                   f"M{code}K01{stamp}.pdf",                                    # CNMV quarterly state (verified)
                   f"M{code}C01{stamp}.pdf", f"E{code}C01{stamp}.pdf"):         # annual accounts (verified)
            got += fetch_pdf(base + nm, out / nm)
    return got

# ------------------------------------------------------------------ CNMV / TdA
OIR = "https://www.cnmv.es/portal/otra-informacion-relevante/resultado-oir.aspx?lang=es&nif={nif}&page={p}"
HR_OLD = "https://www.cnmv.es/portal/hr/resultado-hr.aspx?nif={nif}&division=3&page={p}"   # pre-02/2020 archive
TDA = {  # isin: (ticker, fund name as registered at CNMV, NIF or None)
    "ES0377992005": ("TDAC_5_A", "TDA CAM 5", "V84466135"),
    "ES0377993029": ("TDAC_6_A3", "TDA CAM 6", "V84664358"),
    "ES0377994019": ("TDAC_7_A2", "TDA CAM 7", "V84851724"),
    "ES0377994027": ("TDAC_7_A3", "TDA CAM 7", "V84851724"),
    "ES0377966009": ("TDAC_8_A", "TDA CAM 8", "V85017986"),
    "ES0377955010": ("TDAC_9_A2", "TDA CAM 9", "V85151918"),
    "ES0359091016": ("CAJAM_2006-1_A2", "MADRID RMBS I", "V84889229"),
}
def resolve_nif(name):
    soup = html("https://www.cnmv.es/portal/Consultas/FTA/Listado_ROFT.aspx")
    if not soup: return None
    for a in soup.find_all("a", href=True):
        if a.get_text(" ", strip=True).upper().startswith(name.upper() + ","):
            m = re.search(r"nif=([A-Z0-9]+)", a["href"], re.I) or re.search(r"numreg=(\d+)", a["href"], re.I)
            if m and m.group(1)[0].isalpha(): return m.group(1)
            sub = html(urljoin("https://www.cnmv.es/portal/Consultas/FTA/", a["href"]))
            if sub:
                m = re.search(r"nif=([A-Z]\d{8})", str(sub))
                if m: return m.group(1)
    return None

def get_manifest(isin, out):
    """Every URL already located for this ISIN in filing_locations.csv (incl. CNMV AUDITA annual accounts,
    which live on internet.cnmv.es and are NOT behind the portal's bot protection)."""
    got = 0; man = HERE / "filing_locations.csv"
    if not man.exists(): return 0
    for r in csv.DictReader(open(man, encoding="utf-8")):
        u = r["url"]
        if r["isin"] == isin and u.startswith("http") and u.lower().endswith(".pdf"):
            got += fetch_pdf(u, out / re.sub(r"[^\w.\-]", "_", u.split("//")[1])[-120:])
    return got

AUDITA_HINT = "Annual accounts of every Spanish fund since 2008: https://internet.cnmv.es/AUDITA/<year>/<n>.pdf - find <n> via the CNMV entity page 'Informes financieros anuales' or a web search '<fund name> internet.cnmv.es AUDITA'"

def get_cnmv(isin, out, keep=("FECHA DE PAGO", "PAGO", "LIQUIDACI", "INFORMACION A LOS INVERSORES",
                              "INFORMACIÓN PÚBLICA PERIÓDICA", "INFORMACION PUBLICA PERIODICA", "CUENTAS ANUALES")):
    tk, name, nif = TDA[isin]
    nif = nif or resolve_nif(name)
    if not nif:
        print(f"   {tk}: NIF not resolved - search '{name}' at https://www.cnmv.es/portal/Consultas/FTA/Listado_ROFT.aspx"); return 0
    got = 0
    for tmpl in (OIR, HR_OLD):
        for p in range(0, 200):
            soup = html(tmpl.format(nif=nif, p=p))
            if not soup: break
            links = [(a.get_text(" ", strip=True), a["href"]) for a in soup.find_all("a", href=True)
                     if "verdocumento" in a["href"].lower()]
            if not links: break
            for title, href in links:
                if not any(k in title.upper() for k in keep): continue
                u = urljoin("https://www.cnmv.es/", href)
                name_ = re.sub(r"\W", "", href)[-36:] + ".pdf"
                got += fetch_pdf(u, out / name_)
    return got

# ------------------------------------------------------------------ parse
SERIES = {"ES0377992005": "A", "ES0377993029": "A3", "ES0377994019": "A2", "ES0377994027": "A3",   # CNMV prospectus / BME
          "ES0377966009": "A", "ES0377955010": "A2", "ES0359091016": "A2"}

def parse_folder(isin, folder):
    rows = []
    for pdf in sorted(folder.glob("*.pdf")):
        txt = H.pdf_to_text(pdf)
        (pdf.with_suffix(".txt")).write_text(txt, encoding="utf-8")
        try:
            if isin in MAGELLAN and "Security Level Information" in txt:
                r = H.parse_bcp(txt, isin, pdf.name)
            elif isin in SANT_PAGES and "VALORES EMITIDOS" in txt.upper():
                r = H.parse_uci(txt, isin, pdf.name)
            elif isin in TDA and "liquidaciones intermedias" in txt.lower():
                rows += H.parse_tda_accounts(txt, isin, pdf.name, serie=SERIES[isin]); continue
            elif isin in TDA and isin in txt:
                r = H.parse_tda_notice(txt, isin, pdf.name)
            elif isin in HIPO:
                rs = H.parse_edt_accounts(txt, isin, pdf.name); rows += rs; continue
            else:
                continue
            if r: rows.append(r)
        except Exception as e:
            rows.append({**{k: "" for k in H.SCHEMA}, "isin": isin, "source_url": pdf.name, "parse_status": f"ERROR:{e}"})
    return rows

ALL = {**{k: v[0] for k, v in MAGELLAN.items()}, "ES0380957003": "UCI_15_A", "ES0338186010": "UCI_16_A2",
       **{k: v[0] for k, v in HIPO.items()}, **{k: v[0] for k, v in TDA.items()},
       "XS0230694233": "LUSI_4_A", "XS0268642161": "LUSI_5_A", "XS0312981649": "LUSI_6_A"}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only", default=""); ap.add_argument("--no-parse", action="store_true")
    a = ap.parse_args()
    root = HERE / "reports"; (HERE / "csv").mkdir(exist_ok=True)
    for isin, tk in ALL.items():
        if a.only and a.only.upper() not in tk.upper(): continue
        out = root / f"{isin}_{tk}"; out.mkdir(parents=True, exist_ok=True)
        print(f"{tk} ...")
        n0 = get_manifest(isin, out)
        if isin in MAGELLAN: n = get_magellan(isin, out)
        elif isin in SANT_PAGES: n = get_santander(isin, out)
        elif isin in HIPO: n = get_edt(isin, out)
        elif isin in TDA: n = get_cnmv(isin, out)
        else: n = len(list(out.glob("*.pdf"))); print("   manual source (Citi investor reporting) - drop PDFs in", out)
        print(f"   {n + n0} PDFs ({n0} from manifest)")
        if not a.no_parse:
            rows = parse_folder(isin, out)
            seen, uniq = set(), []
            for r in sorted([r for r in rows if r.get("payment_date")], key=lambda r: r["payment_date"]):
                if r["payment_date"] not in seen: seen.add(r["payment_date"]); uniq.append(r)
            with open(HERE / "csv" / f"{isin}_{tk}.csv", "w", newline="", encoding="utf-8") as h:
                w = csv.DictWriter(h, fieldnames=H.SCHEMA, extrasaction="ignore"); w.writeheader(); w.writerows(uniq)
            bad = sum(1 for r in uniq if not str(r.get("parse_status", "")).startswith("ok"))
            print(f"   {len(uniq)} IPDs parsed, {bad} flagged")
    with zipfile.ZipFile(HERE / "RMBS_reports_ALL.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*.pdf"): z.write(p, p.relative_to(root))
    with open(HERE / "download_log.csv", "w", newline="") as h:
        w = csv.writer(h); w.writerow(["url", "status", "bytes", "sha256"]); w.writerows(LOG)
    print("done ->", HERE / "RMBS_reports_ALL.zip")

if __name__ == "__main__":
    main()
