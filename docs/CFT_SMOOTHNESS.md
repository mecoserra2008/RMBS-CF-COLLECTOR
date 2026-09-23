# CFT-convention grid ranked by smoothness

Stage `cft` of `python -m rmbs.run` (code: `rmbs/cft/`, settings: `config/cft.yaml`, outputs: `out/cft/`).

## What is replicated from Bloomberg CFT, and what cannot be
| Layer | Replicated | Notes |
|---|---|---|
| Prepay conventions | CPR, SMM, PSA (0.2%/month ramp to 6% at month 30), ABS (% of original count) | exact market definitions; SMM = CPR monthly, so only CPR is enumerated |
| Default conventions | CDR, MDR, SDA (standard 30/60/120-month curve) | exact definitions |
| Loss timing | severity, recovery lag, servicer interest advancing (reimbursed from liquidation) | CFT ordering: default -> scheduled -> prepay |
| Scheduled amortisation | level annuity per repline, remaining term = WAM - t | WAM implied from the filed scheduled principal where printed |
| Waterfall | fees, A/sub interest, PDL cured by excess spread, pro-rata / sequential, clean-up call, call date, legal final | **deal models are Bloomberg's proprietary code**: ours is written from the prospectus terms in `config/deals/` |
| State | last FILED IPD (pool, class balances, PDL, index) | Bloomberg's state is its own (stale) tape - the source of its errors |

Therefore: same conventions + same waterfall + same state => same projection. `out/cft/bbg_replication.csv` tests
this directly for every captured Bloomberg run: `engine_only` (our engine, Bloomberg's settings and starting balance)
isolates convention/waterfall differences; `filed_state` (same settings, filed balance) adds the state effect.

## Grid
Every combination of prepay setting x default setting x (severity x lag x advancing, only when defaults > 0) x call
(maturity / clean-up / manager-stated date) x trigger (as filed / forced sequential / forced pro-rata, only where the
deal can amortise pro-rata) x index shift. Where the filings do not print the pool (bond-level ISINs, mode `proxy`),
the repline inputs WAC and WAM are searched as grid dimensions too. ~24k configurations per ISIN (pool mode),
~48-96k (proxy mode).

## "Correct = smooth": the definition used
Smoothness in isolation is degenerate: every constant-CPR projection is perfectly smooth and CPR = 0 is the smoothest
of all. So smoothness is measured **as a continuation of the filed history**, on the class-A paydown rate
rho_t = principal_t / balance_{t-1}:

- `seam_level`: jump between the trend of the last 8 filed rates and the first projected rate (in s.d. of the filed rates);
- `seam_slope`: kink between the filed trend and the first projected year;
- `roughness`: RMS of the second differences of the projected rates (wiggles), terminal redemption excluded;
- `terminal`: size of the final balloon (reported, weight 0: a contractual call is not noise).

S_smooth = seam_level^2 + seam_slope^2 + roughness^2.

**Identification.** Smoothness pins down the *total* paydown rate only: CPR 10% + CDR 0 and CPR 6% + CDR 5% (cured by
excess spread) produce almost the same class-A path. Where the filing prints collateral facts, a consistency term
removes the ambiguity: C = z_cpr^2 + z_cdr^2 + 4 x (forced trigger mode contradicting the filed pro-rata test), with
z = (config - filed) / tolerance. Ranking score S = S_smooth + C. Without filed facts (TDA CAM, Hipocat, bond-level
UCI) the split is **not identified** and `summary.csv` says so; the equivalence class (configurations within 0.05 of
the best) shows how wide the admissible WAL range is.

## Is smoother actually more correct? (out-of-sample check)
`out/cft/criterion_check.csv`: at every historical origin, using only filings published by then, all configurations
are ranked by S and compared with the class-A balance filed afterwards. Spearman(S, error) > 0 means smoother
configurations were closer. See the summary table in the README section of the last run (`out/cft/summary.csv`).
