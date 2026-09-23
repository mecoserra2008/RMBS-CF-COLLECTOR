# Model-selection protocol — how we decide which cash-flow projection to trust, per ISIN

Companion to BLOOMBERG_CFT_METHODOLOGY.md. Everything here is implemented in `rmbs/selection/` and runs unattended.

Principle: **no assumption is chosen by opinion.** Every projection model for every ISIN is fitted on a truncated
history, scored out of sample against realised issuer-reported cash flows, and ranked. The champion is the model that
wins the score; if no model clears the gates, the ISIN is marked `NO_RELIABLE_MODEL` and is excluded from pricing.

---------------------------------------------------------------------------------------------------------------------
## 1. What is being predicted

For ISIN *i* and IPD *t* the engine predicts the **bond** cash flow vector
`ŷ_{i,t} = (principal_{i,t}, interest_{i,t}, end_balance_{i,t})`
from information available strictly before *t*. The realised `y_{i,t}` comes from the issuer filings in `out/*.csv`
(validated rows only: `parse_status` starting `ok`). Collateral-level series (pool balance, CPR, defaults, arrears) are
predicted too where reported, because they are the transmission channel and diagnose *why* a bond forecast failed.

Two horizons, both scored:
- **h = 1** IPD ahead (next-payment accuracy → factor accuracy, the thing Bloomberg gets wrong).
- **h = 4, 8, 12** IPDs ahead (cash-flow-timing accuracy → WAL and DM accuracy).

---------------------------------------------------------------------------------------------------------------------
## 2. Candidate model space

Every candidate is a prepayment/default/recovery specification plus the deal waterfall (the waterfall is not a
candidate — it is code validated by the reproduction gate).

**Prepayment (CPR) candidates**
| id | Specification |
|---|---|
| `cpr_last` | last observed single-period CPR (naive, the benchmark to beat) |
| `cpr_ma3` / `cpr_ma6` / `cpr_ma12` | trailing mean over 3/6/12 IPDs |
| `cpr_ewma` | exponentially weighted, half-life fitted on the training window |
| `cpr_ramp` | linear ramp from current to a long-run level, level and slope fitted |
| `cpr_season` | seasoning curve (age-dependent), pooled across the universe where per-deal data is thin |
| `cpr_rate` | refi-incentive: `CPR = a + b·(WAC − r_mkt) + c·age`, `r_mkt` from the Euribor curve |
| `cpr_ar1` | AR(1) in logit(SMM) with drift, estimated per deal |
| `cpr_regime` | 2-state Markov switching (high/low prepay), fitted only if ≥24 observations |
| `cpr_pooled` | hierarchical/shrinkage estimator: deal estimate shrunk to the country-vintage mean (James–Stein weight) |

**Default (CDR) candidates**: `cdr_zero` (reference only), `cdr_last`, `cdr_ma12`, `cdr_rollrate` (Markov on arrears
buckets), `cdr_logit_arrears` (`CDR = f(90d+ ratio, unemployment proxy, age)`).

**Recovery**: `rec_none`, `rec_lag_fixed(L)` with `L ∈ {12, 18, 24, 36}` months and severity `S ∈ {0.25 … 0.60}`
estimated from realised recoveries where both series exist.

**Call**: `call_never`, `call_at_threshold` (clean-up %), `call_issuer_stated` (where the manager publishes an expected
call date, e.g. UCI 15's Dec-2027).

A candidate model = (cpr, cdr, recovery, call). Restrict the grid to combinations that pass the reproduction gate;
cap the grid at ~200 per ISIN, expanded lazily by score.

---------------------------------------------------------------------------------------------------------------------
## 3. Backtest design (rolling origin, strictly causal)

```
for each ISIN i:
    T_i = validated IPDs, sorted
    warmup = max(8, 0.4 * |T_i|)
    for origin o in T_i[warmup:-1]:
        fit every candidate on T_i[:o]          # no data at or after o
        project h = 1, 4, 8, 12 IPDs            # using only index fixings known at o (forward curve as of o)
        score against realised T_i[o:o+h]
```
Rules that must hold in code and be unit-tested:
- **No leakage**: the realised pool balance at `o+1` may never enter the `o`-origin fit; the index path uses the curve
  as of `o` (or, if no historical curve is stored, the *realised* index is used only for interest and that fact is
  recorded in the run config — never for prepayment fitting).
- **Filing-lag realism**: an issuer report for IPD *t* appears with a lag (days, from `filing_locations.csv`
  publication dates); the fit at origin *o* may use only reports published by *o*.
- Deals with `|T_i| < 12` validated IPDs get **pooled** scoring only (shrinkage estimators) and are flagged
  `THIN_HISTORY`.

---------------------------------------------------------------------------------------------------------------------
## 4. Scoring

Per ISIN, horizon and candidate, on the out-of-sample set:

| Symbol | Metric | Definition | Why |
|---|---|---|---|
| `E_fac` | factor error | mean \|F̂_t − F_t\| in bp of original balance | what Bloomberg gets wrong |
| `E_prin` | principal MAE, normalised | mean \|P̂_t − P_t\| / mean(P_t) | payment size |
| `E_cum` | cumulative principal error at h | \|Σ P̂ − Σ P\| / Σ P | timing drift |
| `E_wal` | WAL error | \|WAL(ŷ) − WAL(y_realised-to-date + actual future where known)\| in years | pricing impact |
| `E_dm` | DM error | \|DM(ŷ, price) − DM(y, price)\| in bp at the observed clean price | the number we trade on |
| `U` | Theil's U | RMSE(model) / RMSE(`cpr_last` naive) | must be < 1 or the model is noise |
| `CRPS` | probabilistic score | CRPS of the predictive distribution (bootstrap or model-implied) | rewards honest uncertainty |
| `COV` | interval coverage | fraction of realised values inside the 80% band, target 0.80 ± 0.08 | calibration |
| `BIAS` | signed error | mean (P̂ − P)/mean(P) | systematic over/under-projection |
| `STAB` | parameter stability | sd of the fitted key parameter across origins / its mean | overfit detector |

**Composite score** (lower is better), weights in `config/scoring.yaml` so they are auditable and tunable:

```
S = 0.30·z(E_dm) + 0.20·z(E_wal) + 0.20·z(E_prin@h1) + 0.15·z(E_cum@h8)
  + 0.10·z(CRPS)  + 0.05·z(STAB)
  + penalties
penalties: +0.50 if U ≥ 1            (worse than naive)
           +0.30 if |COV − 0.80| > 0.15
           +0.25 if |BIAS| > 0.10
           +0.10 per free parameter beyond 2   (parsimony)
```
`z(·)` = z-score across candidates *within* the ISIN. Ties (ΔS < 0.05) are broken by fewer parameters, then by better
`E_dm`. The full score table per ISIN is written to `out/model_scores/<ISIN>.csv` — never just the winner.

---------------------------------------------------------------------------------------------------------------------
## 5. Acceptance gates (a champion must pass all)

| Gate | Requirement |
|---|---|
| A1 | The engine passed the reproduction gate G1–G6 (BLOOMBERG_CFT_METHODOLOGY.md §4) for this ISIN |
| A2 | `U < 1` at h = 1 and h = 4 |
| A3 | `E_fac` ≤ 25 bp of original balance at h = 1 |
| A4 | `E_dm` ≤ 10 bp at h = 4 |
| A5 | Coverage within 0.80 ± 0.08 |
| A6 | Champion wins on ≥ 60% of rolling origins (not just on average) |
| A7 | No look-ahead: the leakage unit tests pass |

Fail → `NO_RELIABLE_MODEL`, the ISIN is reported with realised history only, and priced only under an explicit
user-chosen scenario labelled as such.

---------------------------------------------------------------------------------------------------------------------
## 6. Output of the selection stage

- `out/champion_models.csv` — `isin, cpr_model, cdr_model, recovery, call, params_json, S, U_h1, E_dm_h4, E_fac_h1, coverage, n_origins, gates_passed, fitted_at`
- `out/model_scores/<ISIN>.csv` — every candidate's full score vector.
- `out/uncertainty/<ISIN>.csv` — fan chart: p5/p25/p50/p75/p95 of principal, balance and DM by IPD, from a block
  bootstrap over residuals (≥1,000 draws, seed fixed).
- `out/bbg_vs_model.csv` — where a Bloomberg run exists: `isin, metric, bbg, model, realised, winner`. Bloomberg is
  scored with exactly the same metrics on the same origins. This is the deliverable that proves the point.
- `reports/<ISIN>.html` — one page per ISIN: history, gates, champion, fan chart, Bloomberg comparison.

---------------------------------------------------------------------------------------------------------------------
## 7. Autonomy and test requirements (no human at a terminal)

The run must be safe to start and walk away from.

1. **Entry point**: `python -m rmbs.run --config config/run.yaml` performs harvest → parse → validate → gate → fit →
   score → select → price → report, and returns exit code 0 only if every stage's invariants held.
2. **Non-interactive**: no `input()`, no prompts, no notebook steps, no manual downloads inside the run. Anything
   needing a human (Citi portal, RMS-protected Excel) is detected, logged as `MANUAL_REQUIRED` and skipped, never
   blocking the rest.
3. **Determinism**: every seed fixed in the config; the same inputs must produce byte-identical `out/` (test `test_determinism.py` runs the pipeline twice on a fixture).
4. **Idempotent + resumable**: downloads cached with sha256; parsed text cached; a re-run after a crash continues.
5. **Network resilience**: retries with exponential backoff, ≥0.4 s spacing, per-host rate limits, total timeout per
   file; a failed host degrades that ISIN to `SOURCE_UNAVAILABLE` without killing the pipeline.
6. **Schema enforcement**: pandera (or equivalent) schema on every CSV; violations fail the stage loudly.
7. **Invariants asserted at runtime, not just in tests**: balance identity, factor monotonicity (non-increasing),
   principal ≥ 0, coupon ≥ floor, Σ tranche balances ≤ pool balance, no future-dated rows.
8. **Test suite** (`pytest -q`, must be green before and after any change, and in CI):
   - parser fixtures — one per document layout, asserting exact numbers (already: Magellan ×6, UCI ×2, EdT ×2, TdA ×1);
   - waterfall unit tests per deal against ≥8 historical IPDs;
   - leakage tests (shuffle future data → scores must not change);
   - pricing tests: DM↔price round-trip, WAL vs closed form on a flat amortiser, Act/360 accrual;
   - property tests (hypothesis): any CPR/CDR/severity in range → balances non-negative, monotone, cash flows finite;
   - golden-file test of the whole pipeline on a 3-ISIN fixture subset.
9. **Coverage ≥ 85%** on `rmbs/` excluding downloaders; downloaders tested against recorded HTTP fixtures (`vcrpy`).
10. **Logging**: structured JSON lines to `logs/run_<ts>.jsonl` (stage, isin, event, timing, sha256), plus
    `logs/run_<ts>.md` human summary. Every number in the final report traceable to a source URL + page.
11. **Fail-safe defaults**: if a champion cannot be selected, the pipeline still emits history, gates and the report,
    with the ISIN flagged. It never silently substitutes a default assumption.
12. **Final self-check**: `python -m rmbs.selfcheck` re-validates every output CSV against its schema and the
    invariants, and re-prices two ISINs against stored expected values. The run's exit code depends on it.
