# Pipeline specification — Python parser + projection engine, built to run unattended

Scope: the code Claude Code must produce. Sources and verified facts are in HANDOFF.md; Bloomberg semantics in
docs/BLOOMBERG_CFT_METHODOLOGY.md; scoring in docs/MODEL_SELECTION_PROTOCOL.md.

---------------------------------------------------------------------------------------------------------------------
## 1. Module layout

```
rmbs/
  sources.py        registry: ISIN → deal, manager, source family, URL patterns, NIF, series map
  locate.py         resolve + VALIDATE document locations (§2), maintain filing_locations.csv
  fetch.py          cached, rate-limited, retrying downloader (sha256, ETag, resumable)
  extract.py        PDF → text (pdfplumber; OCR fallback ocrmypdf/pytesseract for scanned accounts)
  parsers/          bcp.py  uci.py  edt_accounts.py  tda_accounts.py  tda_notice.py  santander_accounts.py
  schema.py         SCHEMA + pandera schemas + invariants
  validate.py       row identities, chain continuity, year-end checkpoint reconciliation
  deals/            one module per deal: waterfall, triggers, reserve, PDL, coupon (margin, step-up, floor), call
  collateral.py     scheduled amortisation, CPR/SMM, CDR, recovery lag, severity, arrears roll-rates
  engine.py         collateral → waterfall → bond cash flows; pricing (DM, WAL, spread duration, accrued)
  selection/        candidates.py  backtest.py  metrics.py  score.py  bootstrap.py
  bbg/              ingest.py (cashflows.csv, runs.csv, replines, loan level), compare.py
  report.py         per-ISIN HTML + COVERAGE.md + workbook refresh (openpyxl, formulas preserved)
  run.py            orchestrator; selfcheck.py; cli.py
tests/              unit, property (hypothesis), leakage, golden-file, vcr HTTP fixtures
config/             run.yaml, scoring.yaml, deals/<isin>.yaml (prospectus terms)
```

Rule: **parsers never fetch, fetchers never parse, engine never reads the network.** Each stage's output is a file with
a schema, so any stage can be re-run alone.

---------------------------------------------------------------------------------------------------------------------
## 2. Locating documents, and proving the location is right

`filing_locations.csv` (172 rows today) is the map: `isin, ticker, deal, doc_type, period, url, status,
where_principal_is`. Before anything is parsed, every row is validated:

| Check | Rule | On failure |
|---|---|---|
| L1 reachable | HTTP 200 and `%PDF-` magic (or the expected content type) | mark `DEAD`, try the alternate pattern set, then web-search the filename |
| L2 identity | the ISIN, or the fund name + the period, appears in the extracted text | mark `MISMATCH`, never parse |
| L3 period | the payment/reporting date found in the text matches `period` | correct `period` from the document, log the correction |
| L4 content | the section named in `where_principal_is` exists (regex per doc_type) | mark `LAYOUT_UNKNOWN`, route to the layout queue |
| L5 integrity | sha256 recorded; re-downloads compared; a changed file is re-parsed and diffed | log `RESTATED`, keep both |
| L6 coverage | per ISIN: the set of periods covers every IPD from first to latest with no gap | emit the gap list to `out/gaps.csv` and search for the missing documents |

Discovery for gaps, in order: (1) known URL patterns per source family; (2) the issuer/manager index page;
(3) CNMV AUDITA by web search `"<fund name>" internet.cnmv.es AUDITA`; (4) CNMV OIR by NIF; (5) web search of the
expected file name. Everything found is appended to `filing_locations.csv` with `status='discovered'` and revalidated.

**The document is the citation.** Every parsed row carries `source_url` + `source_section`; a row without both is
invalid by schema.

---------------------------------------------------------------------------------------------------------------------
## 3. Parser contract

```python
def parse(text: str, isin: str, url: str, *, series: str | None = None) -> list[Row]
```
- Pure function of `(text, isin, url, series)`, no I/O, no globals, no network.
- Returns zero or more rows conforming to `schema.SCHEMA`; never raises on a merely unknown layout — returns
  `parse_status='review: <reason>'` so the pipeline can continue and the layout queue can pick it up.
- Every number is taken from a named section; no positional guessing across page breaks.
- Locale handled explicitly: `1.234.567,89` (ES/PT) vs `1,234,567.89` (EN); kEUR vs EUR declared per parser.
- Any new layout requires: a fixture in `fixtures/`, an assertion of the exact expected numbers, and a line in
  `docs/LAYOUTS.md` describing how the layout is recognised.

Row-level validation (`validate.py`) applies the identities: beg − principal = end; end/original = factor;
n_notes × per-note = principal; end(t) = beg(t+1); interest = beg × coupon × days/basis; coupon ≥ floor; and the
year-end reconciliation against `out/yearend_checkpoints.csv` and the audited balance sheets.

---------------------------------------------------------------------------------------------------------------------
## 4. Deal configuration (`config/deals/<isin>.yaml`)

Everything the waterfall needs, each field with the prospectus page it came from:

```yaml
isin: ES0377992005
deal: TDA CAM 5, FTA
series: A
original_balance: 1944000000
notes: 19440
denomination: 100000
index: EURIBOR_3M
margin_bp: 12
margin_steps: []              # [{from: 2012-08-15, margin_bp: 26}]
coupon_floor: 0.0             # observed 2016-01 .. 2022-04
day_basis: ACT/360
ipd_rule: {months: [1,4,7,10], day: 26, convention: following}
amortisation: {mode: sequential, switch_to_pro_rata_if: [...]}
triggers: {arrears_90d_ratio: 0.02, cum_defaults: ..., reserve_at_required: true, pool_factor_min: 0.50}
reserve: {initial: ..., required_pct: ..., floor: ...}
clean_up_call_pct: 0.10
legal_final: 2043-10-26
swap: {type: ..., notional_rule: ...}
fees: [{name: gestora, bp: ...}, ...]
sources: [{field: margin_bp, url: ..., page: ...}, ...]
```
A missing field is a hard error for that ISIN (`CONFIG_INCOMPLETE`), not a silent default.

---------------------------------------------------------------------------------------------------------------------
## 5. Engine invariants (asserted every run)

1. Balances non-increasing, non-negative; Σ tranches ≤ pool.
2. Principal paid ≤ principal available; shortfall carried forward when the deal allows deferral.
3. Interest ≥ 0; coupon = max(floor, index + margin(t)).
4. Cash in = cash out each period (waterfall closes to the cent).
5. Projected IPD dates reproduce the historical calendar rule exactly.
6. Under CPR = CDR = 0, the bond amortises to zero by legal final.
7. Monotonicity: higher CPR ⇒ shorter WAL (all else equal); higher CDR ⇒ ≥ loss.

---------------------------------------------------------------------------------------------------------------------
## 6. Definition of done

- Every ISIN except Lusitano: unbroken validated IPD chain, reproduction gate result recorded, champion model selected
  or `NO_RELIABLE_MODEL`, fan chart, Bloomberg comparison where a run exists.
- `pytest -q` green, coverage ≥ 85%, `python -m rmbs.selfcheck` exit 0, two consecutive runs byte-identical.
- `reports/index.html` + `COVERAGE.md` + refreshed workbook + `RMBS_reports_ALL.zip`.
- `docs/FINDINGS.md`: every anomaly (stale Bloomberg factors with the delta per ISIN, principal shortfalls, trigger
  breaches, coupon floors, margin step-ups, expected calls) with the source document for each.
