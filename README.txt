RMBS payment history + projection/model-selection pipeline

  pip install -r requirements.txt
  python rmbs_harvester.py --selftest              # parser fixtures (legacy harness)
  python -m rmbs.run --config config/run.yaml      # harvest -> parse -> validate -> gate -> select -> price -> bbg
                                                   #   -> report -> workbook -> selfcheck   (exit 0 = all invariants held)
  python -m rmbs.run --offline                     # same, cached files only
  python -m rmbs.selfcheck                         # re-validate every output + re-price the golden cases
  python -m rmbs.workbook                          # refresh + LibreOffice-recalc RMBS_CF_Valuation_Model.xlsx
  pytest -q --cov=rmbs                             # tests (coverage >= 85%)

Outputs: out/<ISIN>_<ticker>.csv (history, schema = rmbs_harvester.SCHEMA), out/gaps.csv, out/validation.csv,
out/yearend_reconciliation.csv, out/reproduction_gate.csv, out/champion_models.csv, out/model_scores/,
out/uncertainty/ (fan charts), out/pricing.csv, out/bbg_vs_model.csv, reports/index.html, COVERAGE.md,
docs/FINDINGS.md, RMBS_reports_ALL.zip, download_log.csv.

Place Bloomberg exports here (see docs/BLOOMBERG_CFT_METHODOLOGY.md section 5):
  bbg/cashflows.csv   bbg/runs.csv   bbg/replines/<ISIN>.csv   bbg/loanlevel/<ISIN>.csv
The uploaded EXCEL.xlsx is Purview/RMS-encrypted and unreadable outside the BiG tenant: re-save without the label or export to CSV.
Lusitano 4/5/6: put the Citi investor reports in reports/<ISIN>_LUSI_x/ whenever you have them.
Deal terms: config/deals/<ISIN>.yaml (null = not yet read from the prospectus -> CONFIG_INCOMPLETE).
