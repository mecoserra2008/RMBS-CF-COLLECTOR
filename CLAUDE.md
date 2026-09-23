
# Specs (read in this order, all binding)
1. HANDOFF.md — context, universe, verified facts, source map, open items
2. docs/PIPELINE_SPEC.md — module layout, location validation (L1-L6), parser contract, deal config, invariants, done
3. docs/BLOOMBERG_CFT_METHODOLOGY.md — every CFT assumption: USE / SEED / REBUILD / BLOCK, reproduction gate G1-G6
4. docs/MODEL_SELECTION_PROTOCOL.md — candidate models, rolling-origin backtest, metrics, composite score, gates A1-A7,
   autonomy + test requirements (must run unattended, deterministic, pytest green, selfcheck exit 0)
Bloomberg is a benchmark to be scored, never an input. Filings are the state.

# Read HANDOFF.md first (full context, verified facts, sources, open items).

# Goal
Complete issuer-reported payment history (every IPD since closing) for the 17 ISINs in filing_locations.csv.
Deliverables: csv/<ISIN>_<ticker>.csv (schema = rmbs_harvester.SCHEMA), RMBS_reports_ALL.zip (all source PDFs),
COVERAGE.md, History sheet of RMBS_CF_Valuation_Model.xlsx refreshed.

# Hard rules
- Data only from issuer / fund-manager / CNMV filings. Never Bloomberg, never interpolated.
  Rows derived from the denomination chain must say DERIVED in parse_status.
- A row is 'ok' only if: beg - principal = end (±0.02 EUR; ±1 kEUR for annual accounts),
  end/original = factor, n_notes x per-note = principal (when printed), end(t) = beg(t+1) for consecutive IPDs.
- Magellan 3/4: balance = denomination x 141,375 notes. Coupon floored at 0 from 2018 (Aug-2017 still -0.069%).
  Margin step-ups: MAGEL 3 13->26bp (after 15-Aug-2012 call date), MAGEL 4 14->28bp (between Jul-2015 and Apr-2017).
- Hipocat 11: A2 has principal SHORTFALLS (due >> paid, see annual accounts 'Liquidaciones intermedias');
  record due and paid; A2/A3 split changes over time.
- TDA CAM funds: series names in the accounts are 'SERIE A', 'SERIE A1/A2/A3'... map ISIN -> series from the prospectus (CNMV folletos) before parsing; TDA CAM 5 Serie A = ES0377992005 (Euribor+12bp, IPD 26 Jan/Apr/Jul/Oct, coupon floored at 0 from Jan-2016).
- Keep `python rmbs_harvester.py --selftest` green; add a fixture for every new layout you parse.

# Sources (in order of preference)
1. BCP investor reports (Magellan): ind.millenniumbcp.pt/.../Documents/Magellan{3,4}-InvestorReport/  (12+ file-name variants, see download_all_reports.bcp_names)
2. Santander de Titulización fund pages (UCI 15/16): full list of 'Información Periódica' + annual accounts
3. EdT folder edt-sg.com/intranet/fondos/FGH09|FGH11/
4. CNMV AUDITA annual accounts: internet.cnmv.es/AUDITA/<year>/<n>.pdf (NOT bot-blocked; 2 years of per-IPD
   bond movements per file) - find <n> by web search '<fund name> internet.cnmv.es AUDITA'
5. CNMV 'Otra información relevante' per NIF (TdA payment notices 'INFORMACION FECHA DE PAGO', since 02/2020)
6. Lusitano 4/5/6: per-IPD reports not public (Citi); annual outstanding per class in ESFG SEC filings (EDGAR CIK 906522, 2006-2013) and Novo Banco annual reports (securitisation note) - use only as year-end checks.
7. (old) Lusitano: not public (Citi investor reporting) - leave for the user.

# Loop
1. pip install -r requirements.txt && python rmbs_harvester.py --selftest
2. python download_all_reports.py
3. Inspect download_log.csv and every csv row whose parse_status is not 'ok'
4. Fix URL patterns / parsers, add fixtures, re-run with --only <programme>
5. For every gap in an ISIN's IPD chain: search for the missing report (web search on the issuer site / AUDITA), add it to filing_locations.csv, re-run
6. Stop when every ISIN except Lusitano has an unbroken chain from first IPD to the latest published report
7. Write COVERAGE.md (per ISIN: first IPD, last IPD, #IPDs, gaps, #ok/#derived/#review) and commit
