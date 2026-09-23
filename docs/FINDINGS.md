# Findings

Every item cites the filing it comes from. "Verified" = read in the filing (original session or this one);
"reported" = rating-agency / exchange text seen in search results (to be confirmed against the filing).

## 0. Environment limitation (this run)
The session's egress policy refused every source host (ind.millenniumbcp.pt, internet.cnmv.es, www.cnmv.es,
www.santanderdetitulizacion.com, assets.santandermedia.com, edt-sg.com, www.sec.gov): no PDF could be downloaded
(`download_log.csv`: SOURCE_UNAVAILABLE). The history therefore contains only the rows extracted in the original
session (+ the text captures in `captures/`). All gaps are listed in `out/gaps.csv`; rerun `python -m rmbs.run`
on a machine that can reach those hosts and the pipeline fills them without code changes.

## 1. Bloomberg
- No Bloomberg export in `bbg/` -> every ISIN `MANUAL_REQUIRED` in `out/bbg_vs_model.csv`. Stale-factor deltas cannot
  be quantified until `bbg/cashflows.csv` + `bbg/runs.csv` are dropped in (schema: docs/BLOOMBERG_CFT_METHODOLOGY.md s.5).

## 2. Principal shortfalls
- **Hipocat 11 A2 (ES0345672010)**, 2019 (kEUR, due / paid / shortfall): 15-Jan 57,491 / 2,910 / 54,581;
  15-Apr 61,380 / 2,414 / 58,966; 15-Jul 67,072 / 6,208 / 60,864; 15-Oct 67,545 / 1,954 / 65,591
  ("insuficiencia de fondos disponibles"; AUDITA 2019/18634, verified). Paid/due 3-9%: any model assuming principal
  paid = pool principal overstates A2 cash flows. Workbook: Inputs!G20 (paid/due) + Engine!BE:BF carry-forward.
- Hipocat 11 accounts 2016 trigger annex (MFGH11C01161231): reserve fund 40.87 EUR vs required 28,000,000 EUR
  (exhausted), arrears 2.97% > 1% -> no pro-rata; Series B and C interest deferral conditions met (verified).

## 3. Trigger breaches / amortisation mode
- **UCI 15 A**: 90d+ arrears 16.28m = 8.99% of the pool vs 2% trigger -> "LAS SERIES B y C NO SE AMORTIZAN"
  (Jun-2026 Informacion Periodica, verified). Sequential for A.
- TDA CAM 8: pro-rata only while pool >= 10% of initial; now irreversibly sequential (Fitch Dec-2025, reported).
- TDA CAM 9: B/C/D interest-deferral triggers breached; reserve fund at floor since Oct-2025 (Fitch Dec-2025, reported).
- Madrid RMBS I: A2 sequential (pro-rata condition not met); reserve fund fully depleted Mar-2013 to Apr-2019,
  ~11% of target in 2022 (S&P Jun-2022, reported).
- Magellan 3/4: Pro-Rata Test PASS on every report read (2009, 2025-26); classes A-D paid pro-rata by current balance,
  per-note amounts rounded to the cent (engine reproduces Aug-2026 class A principal 3,753,506.25 exactly).

## 4. Coupon floors
- **Magellan 3 A**: Accrual Rate 0.000% whenever index + 26bp < 0 from Aug-2018 (first read floored IPD) to May-2022;
  **Aug-2017 still -0.069% (not floored)** (verified).
- **Magellan 4 A**: already floored on the Apr-2017 report (index -0.329% + 28bp = -0.049% -> 0.000%) (verified) -
  the two sister deals applied the floor at different dates.
- **TDA CAM 5 A**: coupon floored at 0% from Jan-2016 to Apr-2022 (manager decision; interest paid 0 in 2021-22 except
  Oct-2022 149 kEUR) (verified, AUDITA 2022/19794).

## 5. Margin step-ups
- **Magellan 3 A**: 13bp -> 26bp after the 15-Aug-2012 call date (26bp on every report from Aug-2013) (verified).
- **Magellan 4 A**: 14bp on the Jul-2015 report, 28bp on the Apr-2017 report; the step-up IPD lies in
  (20-Jul-2015, 20-Apr-2017] - the reports in between are needed to pin it (config uses the verified bound).
- No step-up found for any Spanish deal (none reported in the sources read).

## 6. Calls
- **Magellan 3**: pool net of deemed losses 119.36m = **7.96% of the 1,500.0m cut-off pool** (Aug-2026) - below the
  10% clean-up threshold, not exercised. Candidate `call_at_threshold` is therefore contradicted by the filings and
  scores worst in the backtest.
- **Magellan 4**: pool 145.89m = **9.73% of cut-off** (Jul-2026) - also below 10%, not exercised.
- **UCI 15**: pool 180.96m = 12.65% of 1,430.0m; the manager's own projection assumes the clean-up call on
  **20-Dec-2027** (threshold 143m) - used as `call_issuer_stated` (verified, Jun-2026 report).

## 7. Calendar / data anomalies
- Hipocat 11 accounts print 2015-16 movement dates on the 21st-23rd (22.10.2016 is a Saturday) while the rule and the
  2018-19 accounts give the 15th: the 2015-16 dates look like booking dates, not IPDs. Balances still tie to the
  audited 31/12/2015 (338,657) and 31/12/2016 (302,475) kEUR.
- TDA CAM 5 accounts print 27/01, 27/04, 27/07/2021 against an IPD rule of the 26th (all business days).
- TDA CAM 5 chain from kEUR-rounded accounts drifts 1-2 kEUR vs the audited year-ends (within the n x 0.5 kEUR
  rounding bound); flagged, not adjusted.
- UCI 15 legal final: 18-Dec-2045 (Santander asset) vs 18-Dec-2048 (original workbook) - to confirm in the prospectus.
- Lusitano 4 legal final: "December 2048" (ESFG) vs 14-Sep-2048 (oblible).
- ES0377994027 is **TDA CAM 7 Serie A3** (BME "BTN TDA CAM 7-A3 VBLE 02/2049"; Fitch 28-Feb-2023).

## 8. Model selection
- No ISIN has a champion that passes A1-A7: the reproduction gate is INCOMPLETE everywhere (the filings held lack the
  pool/sub-class balances and index fixings needed for G1/G2/G4 on every replayed IPD), and histories are gappy/thin
  (MAGEL 3: 3 usable h=1 origins). All ISINs are `NO_RELIABLE_MODEL` -> price only under an explicit scenario
  (workbook). Score tables: `out/model_scores/<ISIN>.csv`.

<!-- AUTO -->
## Automatic checks (regenerated every run)

- Failed row/chain checks: 0
- Year-end breaks: 1
  - ES0377992005 2020-12-31: chain 290087.0 vs audited 302596.0 kEUR (https://internet.cnmv.es/AUDITA/2022/19794.pdf)
- IPD calendar deviations (observed vs prospectus rule): 7
  - ES0345672010 2016-01-21 vs rule 2016-01-15: DEVIATION +6d
  - ES0345672010 2016-04-22 vs rule 2016-04-15: DEVIATION +7d
  - ES0345672010 2016-07-22 vs rule 2016-07-15: DEVIATION +7d
  - ES0345672010 2016-10-22 vs rule 2016-10-17: DEVIATION +5d
  - ES0377992005 2021-01-27 vs rule 2021-01-26: DEVIATION +1d
  - ES0377992005 2021-04-27 vs rule 2021-04-26: DEVIATION +1d
  - ES0377992005 2021-07-27 vs rule 2021-07-26: DEVIATION +1d
