# Bloomberg CFT / structured-finance methodology → what we can and cannot reuse

Purpose: define, variable by variable, what Bloomberg's cash-flow engine (CFT / YT / SPA / MTGE-family for EUR RMBS)
assumes, which of those inputs we may take as given, which we must reconstruct from filings, and how each is verified.
This is the reference for the projection engine and for the model-ranking protocol (MODEL_SELECTION_PROTOCOL.md).

**Epistemic status.** Bloomberg does not publish the source of its deal models. Everything below marked `[VERIFY]` is a
working hypothesis from observed behaviour and market convention and MUST be confirmed in the terminal (DES, CFT,
CLC/CLP, DOCS, HELP HELP) before it is relied on. Nothing marked `[VERIFY]` may enter a production assumption set
without a terminal screenshot or an export saved under `bbg/evidence/`. Where we could not verify, the pipeline must
fall back to a filing-derived value and record `source='filing'`.

**The known problem.** For this universe Bloomberg's *realised* payment history is wrong (stale factors, missing or
misdated IPDs, occasionally wrong series mapping). A projection engine seeded with a wrong current balance is wrong on
day one regardless of prepayment assumptions. Therefore: **filings are the state, Bloomberg is at most a prior.**

---------------------------------------------------------------------------------------------------------------------
## 1. The three layers of a CFT run

1. **Collateral state** — what the pool is today: balance, WAC, WAM, seasoning, arrears buckets, defaults to date,
   recoveries, repossessed stock.
2. **Collateral projection** — how that pool amortises: scheduled amortisation, prepayment, default, recovery timing,
   severity, index path for floating-rate mortgages.
3. **Liability waterfall** — how collections become bond cash flows: priority of payments, pro-rata/sequential triggers,
   PDL/insufficiency mechanics, reserve fund draws and amortisation, swap/cap flows, fees, clean-up call, step-up.

Bloomberg parametrises (2), *models* (3) internally, and *sources* (1) from the servicer/issuer tapes it receives.
Each layer needs its own verdict, because (1) is where the error is, (3) is where the opacity is.

---------------------------------------------------------------------------------------------------------------------
## 2. Variable map

Legend for **Verdict**:
- **USE** — take from Bloomberg directly, it is a market datum we cannot better.
- **SEED** — may be used as a starting value / prior, but must be re-estimated and validated on filings.
- **REBUILD** — must be reconstructed from filings; Bloomberg's value is not admissible as an input.
- **BLOCK** — do not use at all; unverifiable or known to be wrong for this universe.

### 2.1 Collateral state (layer 1)

| # | Variable | Bloomberg's approach | Default | Verdict | What we do instead | Verification test |
|---|---|---|---|---|---|---|
| 1.1 | Current pool balance | From last tape Bloomberg ingested; vintage varies by deal | Latest available | REBUILD | Issuer report / audited accounts balance at the last IPD in `out/*.csv` | `abs(bbg_pool − filing_pool)/filing_pool`; log per ISIN in `bbg/deltas.csv` |
| 1.2 | **Bond factor / current balance** | Tape-driven; **known stale for this universe** | — | REBUILD | `end_balance` of the last validated IPD row | Exact match required; any delta is a finding, reported not silently fixed |
| 1.3 | Payment/record date calendar | Deal model | — | SEED | IPD calendar implied by the filings (observed IPD dates + prospectus rule) | Projected dates must reproduce the last 12 observed IPDs exactly |
| 1.4 | WAC / WAM / seasoning | Tape | — | SEED | Issuer report WAC; WAM implied from scheduled principal (§3.2) | \|ΔWAC\| ≤ 10 bp vs report, else flag |
| 1.5 | Arrears buckets (30/60/90/365+) | Tape, deal-specific definitions | — | REBUILD | Report arrears table; keep issuer's own bucket definition | Sum of buckets ≤ pool; trigger ratio reproduces the issuer's printed ratio |
| 1.6 | Cumulative defaults / "deemed losses" | Tape | — | REBUILD | Report cumulative default ratio; Spanish deals: "fallidos" in accounts | Reproduce the printed cumulative default ratio to ±1 bp |
| 1.7 | Recoveries / repossessed stock | Often absent for EUR deals | — | REBUILD | Report recoveries line; accounts "activos recibidos por ejecución de garantías" | Recovery ≤ defaults cumulatively |
| 1.8 | Reserve fund level & required level | Deal model | — | REBUILD | Report reserve balance and required amount | Reproduce amortisation/trigger status printed in the report |
| 1.9 | PDL / "insuficiencia de fondos disponibles" | Usually **not modelled** for EUR deals | 0 | REBUILD | Hipocat-style: principal *due* vs *paid* per IPD from accounts (Nota "Liquidaciones intermedias") | Where a shortfall exists, `principal_due > principal_paid`; the engine must carry it forward |
| 1.10 | Loan-level tape (for Scaled Loan Level) | ECB/EDW loan tape, scaled to control balance | — | BLOCK (unless we hold the tape) | Repline-equivalent built from the report's stratification tables | Scaling factor = control balance / Σ tape balance; if we cannot compute it, mode is unusable |

### 2.2 Collateral projection (layer 2)

| # | Variable | Bloomberg's approach | Default | Verdict | What we do instead | Verification test |
|---|---|---|---|---|---|---|
| 2.1 | Prepayment metric | CPR (annualised SMM), PSA, PPC, ABS | CPR | USE (the *metric*) | Same definition: `SMM = 1 − (1 − CPR)^(1/12)`; quarterly deals aggregate 3 months | Reproduce the issuer's printed CPR from its own balances to ±5 bp |
| 2.2 | Prepayment level | Historical default: user-set or deal-median; often 1-yr CPR | Varies | REBUILD | Estimated from our own realised principal series (§3.3 of the protocol) | Backtest score, not a prior |
| 2.3 | Prepayment vector shape | Flat, ramp, or vector | Flat | SEED | Candidate models: flat, ramp, seasoning curve, burnout, rate-incentive, regime-switching | Model competition (MODEL_SELECTION_PROTOCOL.md) |
| 2.4 | Default metric | CDR, SDA, MDR | CDR 0 | USE (metric) | `CDR` on performing balance; convert issuer default ratios to CDR | Cumulative default path must reproduce the report's cumulative ratio |
| 2.5 | Default level | Often **zero by default** | 0 | BLOCK as a default | Estimated from realised defaults + arrears roll-rates | A 0-CDR run is a scenario, never the base case, for deals with 9% 90d+ arrears (UCI 15) |
| 2.6 | Loss severity | User input | 30–40% typical | REBUILD | Implied from realised (defaults, recoveries) where the report gives both; else jurisdiction prior with a wide band | Sensitivity must be reported; never a point estimate without a range |
| 2.7 | Recovery lag | User input | 12–24m | REBUILD | Implied lag from cross-correlation of default and recovery series | Reported with its confidence; ±6m sensitivity mandatory |
| 2.8 | Delinquency roll-rates | Not modelled in plain CFT | — | REBUILD | Markov roll-rate matrix on the report's arrears buckets, where ≥8 periods exist | Out-of-sample bucket reproduction |
| 2.9 | Index path (Euribor) for asset yield & bond coupon | Forward curve or static/flat | Forward | USE | Same: EUR 3m/12m forward curve; store the curve date in the config table | Curve date must equal valuation date; log both |
| 2.10 | Rate scenarios | Parallel shifts, user curves | Base | USE | Same shifts (−100/0/+100 bp) as a stress, not as model selection criteria | — |
| 2.11 | Scheduled amortisation | Annuity from WAC/WAM per repline | — | REBUILD | Annuity with term implied from the *last reported scheduled principal* (already in the Excel engine); Spanish pools: mostly annuity, monthly | Reproduce the report's scheduled-principal line to ±2% |
| 2.12 | Payment interruption / servicer advances | Modelled only where the deal model has it | — | BLOCK | Not modelled; documented as a known omission | — |

### 2.3 Liability waterfall (layer 3)

| # | Variable | Bloomberg's approach | Default | Verdict | What we do instead | Verification test |
|---|---|---|---|---|---|---|
| 3.1 | Priority of payments | Bloomberg-coded deal model, source not published | — | REBUILD | Waterfall coded from the prospectus per deal; unit-tested against ≥8 historical IPDs | Reproduce realised interest and principal per IPD within tolerance (§4) |
| 3.2 | Pro-rata vs sequential triggers | In the deal model `[VERIFY]` | — | REBUILD | Trigger tests coded from the prospectus (arrears ratio, cumulative defaults, reserve level, pool factor ≥50%, seniority) | The engine must reproduce the *observed* amortisation mode on every historical IPD |
| 3.3 | Reserve-fund amortisation & floor | Deal model | — | REBUILD | From prospectus; validated against the report's reserve series | Reproduce reserve balance path |
| 3.4 | Interest deferral / PDL cure order | Deal model, often simplified | — | REBUILD | From prospectus; needed for Hipocat 11 A2 | Reproduce the shortfall series |
| 3.5 | Coupon formula | Index + margin, with step-ups | — | REBUILD | Margin, step-up date, **0% floor** (Magellan from 2018, TDA CAM 5 2016-2022) from filings | Recompute every historical coupon from index+margin+floor; ≤ €0.02/€1m error |
| 3.6 | Clean-up call | Usually assumed exercised at the call threshold `[VERIFY]` | Call | SEED | Model both: to-call and to-maturity; price the *lower* (worst-to-x) and report both | UCI 15: manager's own report prints an expected call date — that is evidence, use it |
| 3.7 | Step-up call behaviour | `[VERIFY]` | — | REBUILD | Economic test: call iff issuer's cost of funds < step-up coupon, with a judgment flag | Report as a scenario, not a base case |
| 3.8 | Swap / cap flows | In the deal model where it exists | — | REBUILD | From prospectus + accounts (derivative line); several of these deals have swaps (TDA CAM 9 shows hedge derivatives) | Reproduce accounts' derivative flows to ±5% |
| 3.9 | Fees & senior expenses | Deal model | — | REBUILD | From accounts: gestora, agente de pagos, administrador, variable commission | Reproduce the accounts' expense line annually |

### 2.4 Pricing conventions (layer 4)

| # | Variable | Bloomberg | Verdict | Ours |
|---|---|---|---|---|
| 4.1 | Discount margin | Standard DM on projected floating cash flows, Act/360 | USE | Same; implemented in the workbook and in the Python engine (cross-checked, max ΔCF 4.7e-9) |
| 4.2 | WAL | Principal-weighted, to call or to maturity — **which one matters** | USE with label | Always report `WAL_to_call` and `WAL_to_maturity` separately |
| 4.3 | Settlement / accrued | T+2, Act/360 | USE | Same |
| 4.4 | Z-spread / OAS | OAS needs a rate model + prepay function | BLOCK for now | DM and static spread only, until the prepay function is validated |
| 4.5 | Price/yield rounding, factor date | Uses its (stale) factor | REBUILD | Our factor from filings — this is the whole point of the project |

---------------------------------------------------------------------------------------------------------------------
## 3. What we explicitly cannot use right away

1. **Bloomberg's current factor/balance** for these ISINs — demonstrably stale/wrong. Everything downstream inherits it.
2. **Bloomberg's Scaled Loan Level mode** — the scaling factor and the loan tape are not reproducible without the ECB/EDW
   tape. Without both, two CFT runs are not comparable and the difference is unexplainable.
3. **Bloomberg's deal model waterfall** — closed source. We may compare against it, never inherit from it.
4. **Its 0% CDR default** — indefensible for pools with 9% 90d+ arrears.
5. **Any CFT output whose configuration table we did not capture** (mode, vintage, CPR, CDR, severity, lag, curve date,
   settlement). An uncaptured run is not a datum; it is an anecdote.
6. **OAS** until the prepayment model itself is validated out of sample.

What we *can* use immediately: the metric definitions (CPR/SMM/CDR/PSA), the forward curve, DM/WAL/accrued conventions,
and Bloomberg's output *as a benchmark to be beaten* in the scoring pipeline.

---------------------------------------------------------------------------------------------------------------------
## 4. Reproduction gate (the engine must earn the right to project)

Before any projection is scored, the engine must reproduce history. For each ISIN, replay the last `min(12, N)` IPDs
with the *known* realised inputs (realised pool principal, realised defaults, realised index fixings) and require:

| Gate | Metric | Tolerance |
|---|---|---|
| G1 | Bond principal per IPD | ≤ 0.5 bp of the bond balance, or ≤ €1,000 |
| G2 | Bond interest per IPD | ≤ €0.02 per €1m |
| G3 | Ending balance chain | exact (it is the filing) |
| G4 | Amortisation mode (pro-rata/sequential) | 100% of IPDs correct |
| G5 | Shortfall/PDL presence | 100% of IPDs correct where the accounts report it |
| G6 | Reserve balance | ≤ 1% |

An ISIN failing G1–G3 is `BLOCKED_FOR_PRICING`: it may be reported, never priced. The gate result goes in
`out/reproduction_gate.csv` and into the final report.

---------------------------------------------------------------------------------------------------------------------
## 5. Capturing Bloomberg for comparison (what to export, once)

If BQL exposes the cash-flow items, export per ISIN and per configuration; otherwise export CFT manually. Two tables:

**`bbg/cashflows.csv`** — `isin, run_id, payment_date, interest, scheduled_principal, prepayment, default, recovery,
loss, ending_balance`
**`bbg/runs.csv`** — `run_id, isin, retrieved_at, cft_mode (REPLINES|SCALED_LOAN_LEVEL), tape_date, settlement_date,
cpr, cdr, severity, recovery_lag, curve_id, curve_date, price, dm, wal, call_assumption, model_version`

Without `runs.csv` a cash-flow file is unusable — two runs that differ only in tape date look identical otherwise.

The uploaded `EXCEL.xlsx` (replines / scaled loan level extract) is **Microsoft Purview/RMS-encrypted** ("Interno",
big.pt) and cannot be opened by any tool outside the BiG tenant. Re-save it without the sensitivity label, or export the
sheets to CSV, then place them at `bbg/replines/<ISIN>.csv` and `bbg/loanlevel/<ISIN>.csv` with a header row. Expected
repline columns (rename to these): `repline_id, balance, wac, wam_months, seasoning_months, rate_type, margin, index,
io_flag, ltv, arrears_bucket, region`. Loan-level: same fields per loan plus `loan_id`. The pipeline must (a) check
`Σ balance` against the control balance and store the scaling factor, (b) refuse to run Scaled Loan Level if the ratio
is outside [0.95, 1.05], recording why.
