# RMBS payment-history harvest + cash-flow valuation — full handoff

Owner: Américo Serra (BiG, Lisbon). Written 22-Sep-2026 at the end of a claude.ai session that could not finish because
(a) its sandbox is blocked from the issuer hosts and (b) reading ~30 annual-account PDFs as text overflows a chat context.
You (Claude Code, running locally with normal internet) have neither limit: download, parse on disk, read only failures.

Read this file fully, then CLAUDE.md (rules + loop). `docs/conversation_transcript_part1.txt` is the raw log of the
first half of the original session (1.8 MB — grep it, never read it whole).

---------------------------------------------------------------------------------------------------------------------
## 1. Goal and why

Estimate the illiquidity premium on senior European RMBS. Bloomberg's payment history / cash-flow projections for these
bonds are wrong, so the user needs:

1. **One CSV per ISIN with EVERY interest payment date (IPD) since closing**, taken only from issuer / fund-manager /
   CNMV filings: balances, principal, interest, coupon, collateral data. Schema = `rmbs_harvester.SCHEMA`.
2. **All source PDFs** in `RMBS_reports_ALL.zip`.
3. **Excel cash-flow valuation model** (`RMBS_CF_Valuation_Model.xlsx`) fed by those CSVs (History tab), with a
   Bloomberg-comparison column (user pastes BBG factors/principal; delta computed).

---------------------------------------------------------------------------------------------------------------------
## 2. Universe (17 ISINs)

| ISIN | Ticker | Fund / issuer | Manager | Primary source |
|---|---|---|---|---|
| XS0222684655 | MAGEL 3 A | Magellan Mortgages No.3 plc (BCP) | BCP | BCP investor reports, quarterly (IPD 15 Feb/May/Aug/Nov) |
| XS0260784318 | MAGEL 4 A | Magellan Mortgages No.4 plc (BCP) | BCP | BCP investor reports, quarterly (IPD 20 Jan/Apr/Jul/Oct) |
| XS0230694233 | LUSI 4 A | Lusitano Mortgages No.4 plc (BES/Novo Banco) | Citi agency | NOT public (Citi investor portal) |
| XS0268642161 | LUSI 5 A | Lusitano Mortgages No.5 plc | Citi agency | NOT public |
| XS0312981649 | LUSI 6 A | Lusitano Mortgages No.6 plc | Citi agency | NOT public |
| ES0377992005 | TDAC 5 A | TDA CAM 5, FTA (NIF V84466135) | TdA | CNMV AUDITA annual accounts; CNMV OIR notices |
| ES0377993029 | TDAC 6 A3 | TDA CAM 6, FTA | TdA | CNMV AUDITA; OIR |
| ES0377994019 | TDAC 7 A2 | TDA CAM 7, FTA (NIF V84851724) | TdA | CNMV AUDITA; OIR |
| ES0377994027 | TDAC 7 (class TBC) | TDA CAM 7, FTA | TdA | same — **confirm which class this ISIN is** (prospectus) |
| ES0377966009 | TDAC 8 A | TDA CAM 8, FTA | TdA | CNMV AUDITA; OIR notice {7507a87b-…} |
| ES0377955010 | TDAC 9 A2 | TDA CAM 9, FTA | TdA | CNMV AUDITA; OIR |
| ES0359091016 | CAJAM 2006-1 A2 | MADRID RMBS I, FTA (Caja Madrid→Bankia→CaixaBank) | TdA | CNMV AUDITA |
| ES0380957003 | UCI 15 A | F.T.A. UCI 15 | Santander de Titulización | Santander fund page: 78 quarterly 'Información Periódica' + annual accounts |
| ES0338186010 | UCI 16 A2 | F.T.A. UCI 16 (NIF V84856236; classes A1,A2,B,C,D,E; €1,819.8m) | Santander de Titulización | Santander fund page + CNMV AUDITA |
| ES0345672010 | HIPO 11 A2 | HIPOCAT 11, FTA (NIF V64478373; BBVA; legal final 15-Jan-2050) | EdT (since 12-Jan-2017, before GAT) | EdT folder FGH11 + CNMV AUDITA |
| ES0345721015 | HIPO 9 A2a | HIPOCAT 9, FTA (A2a €500.0m) | EdT | EdT folder FGH09 + CNMV AUDITA |
| ES0345721023 | HIPO 9 A2b | HIPOCAT 9, FTA (A2b €236.2m) | EdT | same |

---------------------------------------------------------------------------------------------------------------------
## 3. Where the data lives (verified)

### 3.1 BCP — Magellan 3/4
`https://ind.millenniumbcp.pt/pt/Institucional/investidores/securitizacoes/Documents/Magellan{3,4}-InvestorReport/<file>`
(also `/en/`, and `:443` variants appear in search). BCP renamed files many times. **Verified names:**
`YYYYMMMagellan3_InvestorReport.pdf` (2006-2018), `YYYYMM_Magellan4_InvestorReport.pdf`, `Magellan3_InvestorReport_YYYYMM.pdf`,
`Investor_Report_YYYY_MM.pdf`, `Investor_Report_DDMMYYYY.pdf`, `Investor_Report_YYYY_MM_DDMMYYYY.pdf`, `InvestorReport_YYYYMM.pdf`,
`InvestorReport_YYYY-MM.pdf`, `Investor-Report-YYYY-MM.pdf`, `Investor-Report-YYYYMM.pdf`, `Magellan-Mortgages-No4-plc_DDMMYYYY.pdf`.
`download_all_reports.bcp_names()` tries all of them for every IPD month. If a quarter is still missing, web-search
`"Magellan Mortgages No. 3 Report <Month YYYY>"` — search results expose the real filename.
Also: annual financial statements `.../Magellan3-FinantialStatement/Magellan-N3_Finantial-Statement_21102025.xhtml`.

Report layout: Sec.1 "Security Level Information" (Accrual dates/days, Accrual Rate, Euro Reference Rate, Spread,
Denomination, New Denomination for next period, Total Interest Distributions), Sec.2 collateral (beginning balance,
net of deemed losses, principal redemption, scheduled/prepayments, CPR, WAC, arrears), Sec.6 principal distribution.

### 3.2 Santander de Titulización — UCI 15/16
UCI 15 page `https://www.santanderdetitulizacion.com/san/Home/Fondos-de-Titulizacion/uci-15` lists 78 quarterly reports
(Mar-2007 → Jun-2026) + Cuentas Anuales 2021-2025, all on `assets.santandermedia.com` (83 URLs in filing_locations.csv).
English archive reports also exist (`ES-U.C.I._15_<Month><YY>-Archive_Quarterly_Reports-UCI_15-EN.pdf`).
Report p.2: nominal actual + amortisation per bond; p.3: BONOS PRINCIPAL; p.12+: monthly pool/CPR table since 2006.
UCI 16 page: try `.../uci-16`, `.../uci-16/`, `.../uci+16/uci+16`, `/en/...` (the classic path 404'd from the sandbox).

### 3.3 EdT — Hipocat 9/11
`https://edt-sg.com/intranet/fondos/FGH11/` and `/FGH09/`. Naming: `<E|M><code><doc><seq><YYMMDD>.pdf` — monthly
report `EFGH11N01YYMMDD.pdf`, CNMV quarterly state `MFGH11K01…`, annual accounts `MFGH11C01…` (month-end dates).

### 3.4 CNMV AUDITA archive — **the key source for all Spanish funds (not bot-blocked)**
`https://internet.cnmv.es/AUDITA/<year>/<n>.pdf` (sometimes `https://www.cnmv.es/AUDITA/…`). Audited annual accounts of
every Spanish securitisation fund; each file gives **per-IPD principal & interest per series for two years**:
- TdA funds: Nota "Liquidaciones intermedias" → table `Liquidación de pagos de las liquidaciones intermedias d1 d2 d3 d4`
  with rows `Pagos por amortización ordinaria SERIE X`, `Pagos por intereses ordinarios SERIE X` (kEUR) + Nota 9.1
  "Saldo inicial / Amortización / Saldo final" per series class.
- EdT funds: Nota 8 "movimiento de los Bonos" (per series, non-current/current columns, kEUR) + Nota 17 per-date table
  with **Devengado periodo / Liquidado / Insuficiencia fondos disponibles** (principal due vs paid).
`<n>` is not guessable: find it by web search `"<FUND NAME>" internet.cnmv.es AUDITA` (works well) or the CNMV entity
page. Located so far (all in filing_locations.csv): TDA CAM 5 2016/2017/2021/2022; TDA CAM 6 2021; TDA CAM 7
2016/2017/2020/2021; TDA CAM 8 2022; TDA CAM 9 2016/2017/2020/2021/2022/2024; Madrid RMBS I 2012/2014/2015/2019/2020/2021/2022;
Hipocat 9 2017; Hipocat 11 2019. Every missing year must be found the same way (target: every year since closing).

### 3.5 CNMV OIR (payment notices, since 02/2020)
`https://www.cnmv.es/portal/otra-informacion-relevante/resultado-oir?lang=en&nif=<NIF>` — notices
"INFORMACION FECHA DE PAGO DEL FONDO" (per-IPD, per series: saldo inicial, pendiente anterior, amortización, pendiente,
intereses, tipo). Portal may rate-limit; the older "hechos relevantes" archive covers pre-2020.

### 3.6 Lusitano 4/5/6
Per-IPD reports only on Citi Agency & Trust investor portal (login). Public year-end checkpoints: ESFG SEC filings
(EDGAR CIK 906522, Form 6-K/20-F securitisation table: issued/outstanding per class, 2006-2013) and Novo Banco annual
reports (securitisation note). User will drop Citi PDFs into `reports/<ISIN>_LUSI_x/`.

---------------------------------------------------------------------------------------------------------------------
## 4. Facts established (use them; they are verified against filings)

**Magellan 3/4**
- Class A balance = Denomination × **141,375 notes** exactly (verified on every fully-read report, e.g. 873.92×141,375 =
  123,550,440.00). Original A = €1,413,750,000 for both deals.
- Interest = beg × Accrual Rate × days/360 reproduces the reported Total Interest Distribution to the cent.
- **Coupon floor 0%**: Accrual Rate shows 0.000% whenever index+spread < 0 from 2018 to 2022; Aug-2017 still −0.069%
  (floor applied from 2018). Model input `Inputs!G19` = floor.
- **Margin step-up**: MAGEL 3 A 13bp → 26bp after the 15-Aug-2012 call date (10% clean-up call also exists);
  MAGEL 4 A 14bp → 28bp (14bp in Jul-2015, 28bp by Apr-2017).
- MAGEL 3 IPD mid-month (15th/16th/17th, Act/360), reset ~2 business days before (11-13th).
- Collateral CPR (Magellan 3): 4.2-5.4% 2025-26; Magellan 4: 5.2-9.1%.

**Hipocat 11 A2**
- Original €1,083.2m (10,832 notes), Euribor 3m +13bp, IPD 15 Jan/Apr/Jul/Oct. A2/A3 amortise by a date-dependent split
  (A3 took ~3× A2 in 2018-19).
- **Principal shortfall**: 2019 due vs paid (kEUR) Jan 57,491/2,910; Apr 61,380/2,414; Jul 67,072/6,208; Oct 67,545/1,954
  ("insuficiencia de fondos disponibles"). Any model assuming principal paid = pool principal overstates A2 cash flows.
- A2 balances tie to audited 31/12/2015 338,657; 2016 302,475; 2018 230,507; 2019 217,021 (kEUR).

**TDA CAM 5 Serie A (ES0377992005)**
- 19,440 notes × €100k = €1,944m; Euribor 3m +12bp (B +35bp); IPD 26 Jan/Apr/Jul/Oct; sequential A then B;
  legal final 26-Oct-2043; 10% clean-up call. Coupon floored at 0% from Jan-2016 to Apr-2022 (manager decision).
- Paid (kEUR) 2021: 12,509 / 11,835 / 11,382 / 11,445; 2022: 12,077 / 11,449 / 11,678 / 10,157; interest Oct-2022 149.
- Balances 31/12/20 302,596; 31/12/21 255,424; 31/12/22 210,062.

**UCI 15 A**
- Jun-2012: beg 649,794,451 → prin 8,444,305 → end 641,350,145; Jun-2026: 88,627,468 → 6,827,006 → 81,800,463.
- 90d+ arrears trigger breached (9.0% of pool vs 2% trigger) → B/C sequential. Manager's own projection assumes
  clean-up call 20-Dec-2027 (pool €181m vs 10% threshold €143m). 12m CPR 7.45%.
- Jun-2007 total-bond figures used to derive A (PARTIAL row).

**TDA CAM 8 A**: balance after Aug-2020 IPD €306,555,402.92 (16,354 notes, original €1,635.4m).

**Year-end senior balances** (kEUR) for chain checks: `out/yearend_checkpoints.csv`.

---------------------------------------------------------------------------------------------------------------------
## 5. What is in the repo

| Path | What |
|---|---|
| `rmbs_harvester.py` | SCHEMA + parsers: `parse_bcp` (Magellan, tested on 6 reports), `parse_uci` (tested Jun-12, Jun-26), `parse_edt_accounts` (Hipocat CA2016 + CA2019 layouts), `parse_tda_accounts` (TdA CA2022), `parse_tda_notice` (OIR notice, partial fixture), `validate`. `--selftest` runs all fixtures (writes to `selftest_out/`, never `out/`). |
| `download_all_reports.py` | Downloads every report for every ISIN (BCP name variants per IPD, Santander page scrape, EdT naming rule, CNMV OIR by NIF, **all manifest URLs incl. AUDITA**), writes `reports/<ISIN>_<ticker>/`, `RMBS_reports_ALL.zip`, `csv/<ISIN>_<ticker>.csv`, `download_log.csv` (sha256). `--only MAGEL|UCI|HIPO|TDA|LUSI`, `--no-parse`. |
| `snippet_rows.py` | Magellan rows read from report Sec.1 text (denomination-based) — source of 20+ rows in out/. |
| `filing_locations.csv` | 172 located documents (isin, ticker, deal, doc_type, period, url, status, where_principal_is). |
| `out/*.csv` | Current per-ISIN history (see COVERAGE.md), UCI 15 monthly pool (241 months), year-end checkpoints. |
| `fixtures/` | Real report text used by the self-test. Add one for every new layout. |
| `RMBS_CF_Valuation_Model.xlsx` | Model (see §7). Built by `build_model.py` in the original session (not shipped); edit the workbook with openpyxl directly, keep formulas, recalc with LibreOffice. |
| `CLAUDE.md` | Rules + loop + stop condition. |
| `COVERAGE.md` | Per-ISIN coverage table. Keep it updated. |

Current coverage: MAGEL 3 A 23 IPDs (2009→Aug-26), MAGEL 4 A 10 (2010→Jul-26), HIPO 11 A2 16 (2015-16, 2018-19),
TDAC 5 A 8 (2021-22), UCI 15 A 3 (+241 months pool), TDAC 8 A 1 partial; all others 0.

---------------------------------------------------------------------------------------------------------------------
## 6. Validation rules (a row is `ok` only if all hold)
1. beg − principal = end (±€0.02; ±1 kEUR for annual-accounts rows).
2. end / original = pool factor.
3. n_notes × principal per note = principal (when printed).
4. end(t) = beg(t+1) for consecutive IPDs (chain continuity).
5. Chain ties to every audited year-end balance available (yearend_checkpoints.csv / accounts balance sheet).
6. Interest ≈ beg × coupon × days/360 (Act/360) when coupon is known; coupon ≥ floor.
Rows derived rather than read must say `DERIVED` in parse_status; partial rows `PARTIAL`; unresolved `review`.
Never use Bloomberg or interpolation as a data source.

---------------------------------------------------------------------------------------------------------------------
## 7. Valuation model (RMBS_CF_Valuation_Model.xlsx)
Sheets: Deals (static per ISIN), Inputs (ISIN dropdown, CPR/CDR/severity/lag, call mode, price↔DM, settlement
24-Sep-2026, coupon floor G19), Engine (180 quarterly periods: CDR defaults, CPR prepay, scheduled annuity with term
implied from last scheduled principal, recoveries after lag; pro-rata/sequential switch on deal triggers; excess spread
cures PDL; optional clean-up call; legal-final sweep; DM by 8-step Newton; yield via XIRR; spread duration ±10bp),
History (all extracted IPDs + 6 accounting checks + BBG paste columns → delta), Sources (manifest), UCI15_Pool, Curve
(forward-rate paste). Python cross-check of the engine: max |ΔCF| 4.7e-9 €/period. 0 formula errors.
Illustrative outputs at price 98.50: MAGEL 3 A DM 59.5bp WAL 4.87y; MAGEL 4 A DM 57.9bp WAL 5.54y; UCI 15 A DM 100.8bp
WAL 1.78y. Placeholders to replace: current Euribor fixings, flat curve, price 98.50, UCI 15 scheduled principal.
Deals sheet only populated for MAGEL 3/4 and UCI 15 → populate the rest from the harvested CSVs + prospectus terms
(margins, step-ups, floors, triggers, IPD calendar, clean-up call %, legal final). Hipocat 11 needs a
"principal shortfall" option (paid ≠ due).

---------------------------------------------------------------------------------------------------------------------
## 8. Open items (do all)
1. Download everything (`python download_all_reports.py`), zip, parse.
2. Find every missing AUDITA year for every Spanish fund (closing → FY2025) and every missing BCP quarter.
3. Map ISIN → series for TDA CAM 6/7/8/9, Madrid RMBS I, Hipocat 9, UCI 16 from CNMV prospectuses
   (`cnmv.es` folletos; register no. / ISIN list, e.g. Hipocat 9 folleto 8281). Resolve ES0377994027's class.
4. Extend `parse_tda_accounts` for multi-series funds (A1/A2/A3 rows), older account layouts (2009-2015 scanned/OCR —
   use `pdfplumber`, fall back to `pytesseract` if text is empty), and Santander/UCI annual accounts.
5. UCI 15: parse all 83 reports → full quarterly A history. UCI 16: same.
6. Hipocat 9/11: EdT monthly reports + remaining accounts; record due vs paid principal (new columns `principal_due`,
   `principal_shortfall` are allowed — add to SCHEMA and to the History sheet).
7. Tie every chain to the audited year-end balances; fix or flag breaks.
8. Refresh History sheet + Deals sheet in the workbook; recalc; zero errors.
9. Lusitano: leave manual; add year-end checkpoints from ESFG/Novo Banco filings.
10. Final: COVERAGE.md, `RMBS_reports_ALL.zip`, `download_log.csv`, git commit.

---------------------------------------------------------------------------------------------------------------------
## 9. Environment notes
- Hosts to allow: ind.millenniumbcp.pt, assets.santandermedia.com, www.santanderdetitulizacion.com, edt-sg.com,
  internet.cnmv.es, www.cnmv.es, www.sec.gov. Corporate proxy (BiG) may block → set HTTPS_PROXY or run elsewhere.
- Be polite: ≥0.4 s between requests; CNMV portal may throttle — back off, retry later.
- Keep PDFs out of git history (zip / Git LFS / .gitignore `reports/`).
