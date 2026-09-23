Paste everything below the line into Claude Code, started in the unzipped folder.
-----------------------------------------------------------------------------------------------------------------
You are taking over an RMBS data project. Work autonomously until the stop condition is met; do not ask me questions
unless a decision is genuinely impossible without me (then batch them at the end).

1. Read CLAUDE.md, HANDOFF.md, docs/PIPELINE_SPEC.md, docs/BLOOMBERG_CFT_METHODOLOGY.md and
   docs/MODEL_SELECTION_PROTOCOL.md completely. Treat all five as the specification. grep docs/conversation_transcript_part1.txt
   only when you need a detail (never read it whole).
2. Setup: `git init` (if needed), add `.gitignore` with `reports/`, `*.zip`, `selftest_out/`; `pip install -r requirements.txt`;
   `python rmbs_harvester.py --selftest` must pass before and after every parser change.
3. Run `python download_all_reports.py`. Then execute the CLAUDE.md loop:
   - inspect download_log.csv, every csv row not `ok`, and every gap in each ISIN's IPD chain;
   - find missing documents (BCP filename variants; `"<fund>" internet.cnmv.es AUDITA` web searches for every missing
     year since closing; Santander fund pages; EdT folders; CNMV OIR notices), add them to filing_locations.csv, re-run;
   - map every Spanish ISIN to its series from the CNMV prospectus before parsing multi-series accounts;
   - extend parsers (with a fixture + selftest assertion for every new layout); OCR scanned accounts if needed;
   - tie each chain to audited year-end balances (out/yearend_checkpoints.csv + balance sheets); flag breaks, never paper over them.
4. Write final per-ISIN CSVs to out/ (schema = rmbs_harvester.SCHEMA; add principal_due/principal_shortfall if needed),
   build RMBS_reports_ALL.zip, update COVERAGE.md (first IPD, last IPD, #IPDs, gaps, ok/derived/partial/review counts).
5. Update RMBS_CF_Valuation_Model.xlsx: History sheet with all rows, Deals sheet for all 17 ISINs from prospectus terms
   (margins, step-ups, floors, triggers, IPD calendar, clean-up call, legal final), keep every formula, recalc with
   LibreOffice, zero formula errors. Add a principal-shortfall option for Hipocat 11.
6. Build the projection + model-selection pipeline exactly as docs/PIPELINE_SPEC.md and
   docs/MODEL_SELECTION_PROTOCOL.md define it: reproduction gate G1-G6 per ISIN, rolling-origin backtest with no
   leakage, candidate grid, composite score, acceptance gates A1-A7, champion per ISIN, fan charts, Bloomberg
   comparison on identical metrics. It must run unattended end to end: `python -m rmbs.run --config config/run.yaml`,
   deterministic, resumable, non-interactive, `pytest -q` green with coverage >= 85%, `python -m rmbs.selfcheck` exit 0,
   two consecutive runs byte-identical.
7. If bbg/ CSVs are present (replines / scaled loan level / CFT runs), ingest and score them; if absent, continue and
   mark MANUAL_REQUIRED - never block on them.
8. Commit after each fund and each pipeline stage, with the coverage/score delta in the message.

Stop condition: every ISIN except Lusitano 4/5/6 has an unbroken, validated IPD chain, a reproduction-gate result and
either a champion model that passes A1-A7 or an explicit NO_RELIABLE_MODEL flag from first IPD to the latest
published report, tied to all available audited year-ends; Lusitano has year-end checkpoints and an empty folder ready
for the Citi reports. Finish with a short report: coverage table, anomalies found (shortfalls, trigger breaches, floors,
step-ups, calls), and anything that still needs me.
