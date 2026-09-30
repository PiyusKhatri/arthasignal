# Honest Status of the Quant Models

Written 2026-09-30 from the repository contents only. No code was changed and no model was re-run.

**Bottom line.** No model version has passed its own historical gate. Every recorded gate status is `review` and every verdict is `continue_research`. No challenger has been promoted. The only forward snapshot in the repository shows zero forward observations. V5 has no recorded result at all.

## How to read this document

- Citations are `file:line` for docs and code, and `file → key` for JSON result files.
- "Cost" always means the repository's assumed 1.00% round trip. That is an assumption, not a measured NEPSE cost (see section 4).
- The result files carry no model-version field. I attributed each file to a model by matching its gate-check names to the validator that emits them:

| File | Model | Emitting validator |
| --- | --- | --- |
| `extracted_metrics.json` | V2 (`artha-xgb-execution-aware-v2-research`) | `src/pipeline/validate_execution_challenger.py:37,310-335` |
| `v3_extracted.json` | V3 | `src/pipeline/validate_regime_decision_v3.py` |
| `v4_extracted.json`, `v4_extra_extracted.json` | V4 | `src/pipeline/validate_residual_alpha_v4.py:37` |
| `v41_extracted.json`, `v41_portfolio_extracted.json`, `v41_shadow_extracted.json` | V4.1 | `src/pipeline/validate_residual_stability_v41.py`, `validate_v41_portfolio.py`, `v41_shadow.py` |
| `e1_extracted.json`, `e1_diag_extracted.json`, `e1_diagnostics.txt` | E1 | `src/pipeline/validate_execution_policy_e1.py`, `diagnose_execution_policy_e1.py` |

- `extracted_metrics.json` is V2, not V3. V3 lives in `v3_extracted.json`, which was not on the requested reading list but is the only V3 evidence, so I used it.
- The database could not be reached from this machine (`password authentication failed for user "postgres"` against the configured Supabase pooler). Anything that lives only in the database is marked "cannot be determined".

## 1. Each model version

### Summary

| Version | Out-of-sample result after 1% cost | Own gate | Failed checks |
| --- | --- | --- | --- |
| V2 | Top-10 mean net excess +0.44%, precision 42.3% | `review` (not passed) | 2 of 8 |
| V3 | Mean net excess +0.015%, median −2.16%, 33 calls | `review` (not passed) | 6 of 10 |
| V4 | Mean net excess +3.12%, median +0.07%, 942 calls | `review` (not passed) | 3 of 11 |
| V4.1 | Mean net excess +2.75%, median −0.31%, 712 calls | `review` (not passed) | 4 of 11 |
| V5 | No result exists | Not run; default is `research_rejected` | n/a |
| E1 | CAGR +9.79% vs NEPSE +12.06% | `review` (not passed) | 2 of 10 |

Every validator passes only when all checks are true (`passed = all(checks.values())`, for example `validate_execution_challenger.py:335`). `review` is the label for "not passed".

### V2 — execution-aware XGBoost challenger

- **What was tested.** A 4-fold expanding nested walk-forward over 49,457 test rows, test dates 2018-12-13 to 2026-07-23 (`extracted_metrics.json → execution_metrics.samples`, `fold_consistency.folds[*].period`). Target: 20-day stock excess return over NEPSE above the cost hurdle.
- **Result after cost.**
  - Top-10 precision 42.3%, mean net excess +0.44% (`→ top_k.p_at_10`).
  - Best baseline top-10 precision 40.9% (`→ best_stronger_baseline_p10`).
  - Non-overlapping 51-cohort portfolio: mean net excess +0.50%, median −0.12%, 95% interval −0.81% to +1.98% (`→ non_overlapping_portfolio`).
  - High-confidence calls: 32 calls on 28 dates, precision 59.4%, Wilson 95% interval 42.3% to 74.5% (`→ high_confidence`). That is 0.065% coverage (`→ execution_metrics.coverage`).
  - Most recent fold (2024-08-21 to 2026-07-23): top-10 precision 36.1%, mean net excess −0.79% (`→ fold_consistency.folds[3].top_k.p_at_10`).
- **Gate.** Not passed. Failed `most_folds_positive_after_1pct_cost` (2 of 4 folds positive; needs 3) and `non_overlap_bootstrap_lower_bound_positive` (`→ research_gate.checks`, `fold_consistency.positive_after_1pct_cost_folds`).

### V3 — regime-aware, risk-gated decision policy

- **What was tested.** 3 valid outer folds; 65,870 pooled rows reduced to 43,310 investable rows (`v3_extracted.json → summary`). The policy abstained on 95.0% of eligible dates and selected 33 rows on 30 dates.
- **Result after cost.**
  - Precision 45.5%, mean net excess +0.015%, median −2.16% (`→ summary`).
  - The matched-breadth baseline earned +6.97% mean net excess, far more than V3 (`→ summary.matched_breadth_mean_net_excess_percent`).
  - Severe-drawdown rate 51.5%, identical to the baseline; risk reduction 0.0 (`→ risk`).
  - Non-overlap mean −0.21%, 95% interval −2.64% to +2.17%, 13 cohorts (`→ summary`).
- **Gate.** Not passed; 6 of 10 checks failed (`→ research_gate.checks`): positive folds (2, needs 3), beats-baseline folds (2, needs 3), median net excess, mean beats baseline, 15% risk reduction, non-overlap bootstrap.

### V4 — residual-alpha challenger

- **What was tested.** 4 folds, test dates 2020-08-16 to 2026-07-23, compared with a momentum baseline at exactly the same dates and breadth (`v4_extra_extracted.json → fold_consistency.folds[*].period`). 942 selected rows on 391 dates (`v4_extracted.json → summary`).
- **Result after cost.**
  - Precision 50.3%, mean net excess +3.12%, median +0.07%; baseline mean +1.80% (`v4_extracted.json → summary`).
  - Incremental alpha over baseline: mean +1.53%, median +0.09%, date-bootstrap interval +0.48% to +2.65%.
  - Non-overlapping cohorts (43): mean +1.23%, interval −1.73% to +4.27%.
  - Cohort-compounded approximate CAGR: V4 −6.40%, baseline −21.21%, NEPSE +16.74% (`→ summary.v4_approx_cagr_percent` and neighbours).
  - Per fold, V4 beat the baseline in folds 1 and 3 and lost in folds 2 and 4 (`v4_extra_extracted.json → folds[*].incremental.mean_incremental_net_excess_percent`: +2.21, −1.38, +4.29, −1.75).
  - Most recent fold (2025-01-13 to 2026-07-23): precision 26.4%, mean net excess −3.32%.
- **Gate.** Not passed; 3 of 11 checks failed (`v4_extracted.json → research_gate.checks`): beats baseline in at least 3 folds (got 2), 10% risk reduction (got 8.5%), non-overlap bootstrap lower bound.

### V4.1 — residual-stability policy on the V4 architecture

- **What was tested.** The same 4 folds and period as V4 (`v41_extracted.json → fold_consistency.folds[*].period`). 712 selected rows on 312 dates (`→ summary`). Two separate tests: call-level selection, and a daily mark-to-market portfolio simulator.
- **Call-level result after cost.**
  - Precision 48.7%, mean net excess +2.75%, median −0.31%; baseline mean +1.44%, median −0.97% (`→ summary`).
  - Incremental alpha: mean +1.53%, median 0.0, date-bootstrap interval +0.23% to +2.97%.
  - Non-overlapping cohorts (32): mean **−0.58%**, interval −3.33% to +2.13%.
  - Cohort-compounded approximate CAGR: V4.1 +2.41%, baseline +11.79%, NEPSE +40.83% (`→ summary.v41_approx_cagr_percent` and neighbours).
  - Severe-drawdown rate 48.9% against 53.8% for the baseline, a 9.1% relative reduction (needs 10%).
- **Fold detail that weakens the headline.**
  - The "beats baseline in 4 of 4 folds" count includes fold 3, which has 10 selected rows on 2 dates (`→ folds[2].v41`).
  - No fold has a date-bootstrap interval clearly above zero: lows are −0.27, −0.08, 0.0 and −2.43 (`→ folds[*].incremental.incremental_bootstrap_95.low`).
  - Most recent fold: precision 28.3%, mean net excess −3.61%, severe-drawdown rate 67.7% (`→ folds[3].v41`).
  - 579 of 712 selections are `hold` (the baseline's pick kept) and 133 are `promote` (`→ override_diagnostics.actions`). The model mostly reproduces the baseline.
- **Portfolio simulator result** (5 positions, equal weight, 1% cost, 1,365 sessions; `v41_portfolio_extracted.json → summary`).
  - V4.1: total return −67.8%, CAGR −18.9%, max drawdown −86.5%.
  - Matched baseline: total return −87.9%, CAGR −32.3%.
  - NEPSE buy-and-hold: total return +81.5%, CAGR +11.6%.
  - V4.1 turnover 125.6x a year, transaction costs 340% of initial capital, median holding 1 session.
  - V4.1 minus baseline, annualised: +16.6%, interval −9.9% to +41.8%. V4.1 minus NEPSE: −27.7%, interval −60.9% to +3.4% (`→ block_bootstrap`).
- **Gate.** Not passed; 4 of 11 checks failed (`v41_extracted.json → research_gate.checks`): median incremental alpha positive, selected median net excess positive, 10% risk reduction, non-overlap bootstrap lower bound.

### V5 — point-in-time stability challenger

- **What was tested.** Nothing that is recorded. The protocol states "no historical V5 outcome has been admitted under this protocol" (`docs/V5_CHALLENGER_PROTOCOL.md:6`). There is no V5 result file in the repository.
- **Result after cost.** None exists. Whether the validator (`src/pipeline/validate_quant_v5.py`) has ever been run against real data cannot be determined.
- **Gate.** Not evaluated. By its own rule V5 "remains `research_rejected` unless every item below is true" (`docs/V5_CHALLENGER_PROTOCOL.md:177`), so its current standing is rejected-by-default. Forward collection may only begin after a historical pass (`:205`), so V5 has no forward ledger either.

### E1 — low-churn execution policy for V4.1 predictions

- **What was tested.** The frozen V4.1 predictions replayed through a sticky portfolio policy (5 positions, 20-session cap, 0.12 replacement margin, 1% cost) over 1,409 sessions from 2020-08-16 to 2026-08-20 (`e1_diagnostics.txt → period`, `e1_frozen_assumptions`). E1 is an execution policy, not a new predictive model (`docs/E1_EXECUTION_POLICY.md:11-13`).
- **Result after cost** (`e1_extracted.json → summary`).
  - E1 on V4.1: total return +68.6%, CAGR +9.79%, max drawdown −37.4%, turnover 34.25x, mean holding 9.7 sessions.
  - E1 on the momentum baseline: total return −38.1%, CAGR −8.23%.
  - NEPSE buy-and-hold: total return +89.0%, CAGR +12.06%, max drawdown −43.3%.
  - E1 minus baseline, annualised: +15.7%, interval −5.7% to +36.3%. E1 minus NEPSE: −2.5%, interval −26.0% to +21.3% (`→ block_bootstrap`).
  - Turnover fell 71.9% against the reactive V4.1 policy (`→ summary.turnover_reduction_vs_reactive_v41`).
- **Fold and trade detail** (`e1_diag_extracted.json`).
  - Fold 1: E1 +109.7%, baseline +188.0%, so E1 trailed by 78.3 points. Fold 4: E1 −37.4%, baseline −53.7% (`→ fold_diagnostics`).
  - Trades entered in 2025: 29 trades, 27.6% positive, mean gross −3.6%. In 2026: 28 trades, 25.0% positive, mean gross −2.4% (`→ trade_breakdowns.year`).
  - Trades entered on an ML `promote` did worse than `hold` trades: mean gross +0.54% against +2.69% (`→ trade_breakdowns.override_action`).
  - 507 sell attempts were blocked for lack of a trade print (`→ summary.e1_v41.blocked_sell_attempts`). "Expired" trades were held 33.1 sessions on average despite the 20-session cap (`→ trade_breakdowns.exit_reason.expired`).
- **Gate.** Not passed; 2 of 10 checks failed (`e1_extracted.json → research_gate.checks`): annualised turnover at most 30x (got 34.25x) and block-bootstrap lower bound above zero (got −5.7%). The doc acknowledges this: "E1 still missed the historical 30x turnover and block-bootstrap gates, so it is not promoted" (`docs/E1_FORWARD_EXECUTION.md:10`).

## 2. Champion and forward validation

### Champion

- The docs name V1 as "the live champion" (`docs/V41_FORWARD_VALIDATION.md:6`, `docs/V5_CHALLENGER_PROTOCOL.md:12`, `docs/E1_EXECUTION_POLICY.md:5`). V1 is `artha-xgb-cross-sectional-v1` (`src/services/quant_model_store.py:12`).
- V1 is champion by default, not by evidence in this repository. There is no V1 result file, and the V2 validator states "The v1 holdout has already been inspected" (`src/pipeline/validate_execution_challenger.py:374`). V1's historical numbers cannot be determined from the files read.
- V1 is not a frozen model: the daily workflow retrains it when a weekly interval is due (`.github/workflows/daily_pipeline.yml:70-71`).
- V1's own forward gate needs 80 resolved calls, 20 independent days, 30 high-confidence calls at 58% precision or better (`src/pipeline/quant_validation.py:19-24`). Its current state cannot be determined (database unreachable).
- No challenger has earned champion review: V2, V3, V4, V4.1 and E1 are all `review`, and V5 is unrun.

### Forward validation

Four forward streams are defined. Only one has any output in the repository.

| Stream | Maturity threshold | Forward observations on record |
| --- | --- | --- |
| V4.1 shadow ledger | 80 matched resolved calls, 20 matched dates, 12 non-overlapping cohorts (`docs/V41_FORWARD_VALIDATION.md:59`) | **0 rows** as of 2026-08-20 |
| E1 forward execution | 120 signal dates, 40 completed trades (`docs/E1_FORWARD_EXECUTION.md:82-83`) | Cannot be determined; no output file |
| Rule-based signal protocol `2026-08-20-v1` | 60 or 100 graded calls per signal, 20 entry-date clusters (`docs/VALIDATION_PROTOCOL.md:19-22,62`) | Cannot be determined; no output file |
| V1 shadow | see above | Cannot be determined; no output file |

- **V4.1 snapshot** (`v41_shadow_extracted.json`). The single capture is dated 2026-08-20: 293 symbols considered, 178 eligible, **0 candidates**, status `no_current_candidates` (`→ capture`). The ledger has 0 rows, 0 pending, 0 resolved, 0 matched dates (`→ validation.ledger`). Gate status `collecting`; all 8 checks false; every alpha and risk statistic is `null`.
- **What the forward evidence shows so far:** nothing. There is not one resolved forward observation for any model in the repository.
- **Whether capture has run since 2026-08-20** cannot be determined. Points against assuming it has:
  - The production database decision is still open, and scheduled runners cannot reach a laptop-local database (`TODOS.md:94-105`).
  - The V4.1 doc says the workflow change "does not become production-active while this work remains only on the repair branch" (`docs/V41_FORWARD_VALIDATION.md:115`), and the merge into `main` is not yet committed.
- **Earliest possible maturity.** With a 20-session horizon, 12 non-overlapping 20-session cohorts is about 240 trading sessions of uninterrupted daily capture.

## 3. Verified versus only claimed

### Verified by tests (run today on the merged tree)

- `pytest`: 301 passed, 1 failed. The failure is `tests/test_auth_integration.py`, a database login error, not a quant assertion.
- 158 quant test functions exist across `tests/test_quant_*.py`. Only `tests/test_auth_integration.py` touches a database; the quant tests run on synthetic fixtures.
- What those tests establish is code behaviour, for example:
  - future data cannot change V4 features (`tests/test_quant_v4_leakage.py:35`);
  - V5 features are invariant to post-signal observations (`tests/test_quant_v5_leakage.py:54,98,149`);
  - the frozen V4.1 train/calibration split purges forward labels (`tests/test_quant_v41_forward.py:34`);
  - E1 forward decisions shift to the next session's open (`tests/test_quant_e1_forward.py:29,44`).
- No test asserts any performance number. A passing suite says nothing about whether a model makes money.

### Supported by result files, but not reproducible here

- Every number in section 1. The files are named `*_extracted.json`: they are extracted subsets, not raw validator output, and they omit model version, run date and data cutoff. `e1_diagnostics.txt` is the only full output.
- All ten result files entered the repository in a single commit, `771cbb4` (2026-08-21), described as "retained quant diagnostics". They cannot be regenerated without the database.

### Claimed in docs or commit messages only

- **Pre-commitment of gates and parameters.** For example "The 0.12 margin is predeclared before the E1 historical result is observed" (`docs/E1_EXECUTION_POLICY.md:61`). Git cannot confirm it: all 224 incoming commits are dated 2026-08-20 (96) or 2026-08-21 (128), and results were committed only at the end.
- **"V4.1 showed repeatable stock-selection improvement over the transparent momentum baseline"** (`docs/E1_EXECUTION_POLICY.md:9`). The results do not support "repeatable": median incremental alpha is 0.0 and the non-overlap mean is −0.58% (`v41_extracted.json → summary`).
- **E1 "preserving V4.1's relative edge"** (`docs/E1_FORWARD_EXECUTION.md:10`). The interval for E1 minus baseline is −5.7% to +36.3%, which includes zero (`e1_extracted.json → block_bootstrap`).
- **"V4.1 and E1 remain frozen and continue collecting their own forward evidence"** (`docs/V5_CHALLENGER_PROTOCOL.md:12`). The only snapshot shows 0 rows.
- **V5 "independent code review found and corrected four implementation ambiguities"** (`docs/V5_CHALLENGER_PROTOCOL.md:236`). No record of that review is in the files read.
- **Frozen V4.1 artifact and its fingerprint** (`v41_shadow_extracted.json → capture.artifact_fingerprint`). The snapshot lives in the database and could not be inspected.
- **"Both services are live and healthy"** (`docs/ARCHITECTURE.md:335-338`). A point-in-time statement; today the configured database rejects the login.
- **Auth hardening "DONE in repair branch; deployment initialization required"** (`TODOS.md:75`). The code and unit tests exist; the one integration test could not run.

## 4. Leakage risks and biggest weaknesses

### Leakage and selection risks that remain

1. **No untouched holdout exists.** Each validator says so itself: `"fresh_untouched_historical_holdout": False` (`validate_execution_challenger.py:372`, `validate_regime_decision_v3.py:526`, `validate_residual_alpha_v4.py:404`, `validate_residual_stability_v41.py:455`).
2. **Design iterated on the test period.** V4, V4.1 and E1 share the test window 2020-08-16 to 2026-07-23. V4.1 was designed after V4 failed on it, and E1 after V4.1's portfolio failed on it (`docs/E1_EXECUTION_POLICY.md:9`). Policy constants are already on their second revision (`V3_POLICY_VERSION = "…regime-risk-v2"` at `src/services/quant_decision_policy.py:11`; `V4_POLICY_VERSION = "…residual-alpha-v2"` at `src/services/quant_residual_alpha.py:12`). Five successive tries on one period make any eventual pass weaker evidence.
3. **Entry at the signal-day close.** The historical label is `closes[idx + 20] / closes[idx]` (`src/services/quant_features.py:207-208`): the same close that feeds the features is the assumed entry price. The docs concede the forward convention "is stricter than the historical E1 execution diagnostic" (`docs/E1_FORWARD_EXECUTION.md:50`). Historical numbers are therefore optimistic on execution timing.
4. **Survivorship.** 226 of 400 symbols were used and 174 skipped; "Historical delisted/suspended coverage is still incomplete" (`e1_diagnostics.txt → universe`; `src/pipeline/train_quant_models.py:152-154`).
5. **Horizon drift in thin names.** The legacy label resolves after 20 stock observations, not 20 market sessions; "thin-name horizon drift remains a diagnostic limitation" (`e1_diagnostics.txt → investability.note`).
6. **The frozen V4.1 artifact cannot be back-tested.** It was "trained after some historical test dates and would leak future training information if replayed backward" (`docs/V5_CHALLENGER_PROTOCOL.md:163`). Only forward rows count as evidence for it, and there are none.
7. **Sparse historical sampling.** Historical rows were sampled every five stock observations (`v41_portfolio_extracted.json → historical_cadence_limitation`). E1 handles gaps by keeping the last score (`docs/E1_EXECUTION_POLICY.md:67-74`); 2,257 such "sampled absence holds" occurred (`e1_extracted.json → summary.e1_v41.sampled_absence_holds`). That rule lowers measured turnover in exactly the metric E1 is judged on.
8. **Adjusted prices in the simulator.** The price basis is "adjusted close when available, otherwise close" (`v41_portfolio_extracted.json → execution_policy.price_basis`). Whether adjustment factors introduce look-ahead cannot be determined from the files read.

### Biggest weaknesses

1. **Nothing beats buy-and-hold NEPSE.** The best tradable result, E1, returned +9.79% a year against +12.06% for the index. The V4.1 reactive portfolio lost 67.8% while the index gained 81.5%.
2. **The edge over the baseline is not statistically established.** Every non-overlapping or block-bootstrap interval includes zero (V2, V3, V4, V4.1, E1; cited in section 1).
3. **Performance is deteriorating.** The most recent fold is negative for V2 (−0.79%), V4 (−3.32%) and V4.1 (−3.61%), and E1's 2025 and 2026 trades win about one time in four.
4. **The ML overlay adds little.** 81% of V4.1 selections are the baseline's own picks, and `promote` trades underperform `hold` trades in E1.
5. **High downside.** About half of selected calls suffer a drawdown of 5% or more (V4 51.6%, V4.1 48.9%).
6. **Illiquidity is real.** 507 blocked sells in the E1 replay; positions outlive the holding cap.
7. **The cost model is an assumption.** 1.00% round trip for the quant models and 0.50% for the rule-based signals, which "is not a claim that every actual NEPSE trade costs exactly 0.50%" (`docs/VALIDATION_PROTOCOL.md:48`). The docs read describe no slippage, market-impact or tax model.
8. **The statistics are deliberately simple.** The signal protocol calls its interval "a deliberately simple private-validation statistic rather than a publication-grade econometric claim" (`docs/VALIDATION_PROTOCOL.md:64`).
9. **Zero forward evidence, and a long wait for any.** See section 2.
10. **Legal blocker.** Public or paid signals are blocked pending advice from a Nepal securities lawyer (`TODOS.md:7-15`, `docs/VALIDATION_PROTOCOL.md:116`).

## 5. Finished, half-built, broken

### Finished (code exists and its tests pass)

- Historical validators for V2, V3, V4, V4.1 and E1, with recorded results.
- V4.1 frozen-artifact, shadow-ledger and heartbeat code; E1 forward capture and replay code.
- V5 dataset, model, training, validation and replay code with six test files.
- Rule-based signal validation protocol and status CLI (`TODOS.md:31-59`).
- Auth and session hardening code (`TODOS.md:73-84`).
- CI workflow running compile, tests and frontend checks (`TODOS.md:131-135`).

### Half-built

- **V5.** Built and unit-tested; never evaluated on record. No result, no artifact, no ledger.
- **All forward validation.** Pipelines are wired into the daily workflow (`.github/workflows/daily_pipeline.yml:73-80`), but the only evidence is one capture with zero candidates.
- **Production database.** Open decision (`TODOS.md:94-105`). Until it is settled, scheduled capture has nowhere reliable to write.
- **SMTP password-reset delivery.** Open deployment configuration (`TODOS.md:107-111`).
- **Auth deployment.** New tables need `python -m src.database.init_db` after merge (`TODOS.md:86-92`).
- **Payments.** Not started by design (`TODOS.md:17-29`).
- **The merge itself.** Conflicts are resolved in the working tree but the merge is not committed.

### Broken or stale

- **Database access from this machine.** The configured Supabase pooler rejects the password, which fails the auth integration test and blocks every validator and status command.
- **`docs/ARCHITECTURE.md` is out of date.** It describes no quant, intelligence or alerts component: the router table (`:114-132`) and schema section (`:150-202`) omit them, and it says the database is local Docker (`:6`) while this checkout points at Supabase.
- **The repository `venv/`** was created on another machine (`venv/pyvenv.cfg` records `/home/piyus-khatri/...` and `/usr/bin/python3.12`) and does not run here.
- **`requirements.txt` pins `psycopg2-binary==2.9.9`,** which failed to build on Python 3.13, although `docs/ARCHITECTURE.md:262` lists 3.13 as supported.
- **As models:** V3 (6 of 10 checks failed, 33 calls) and the reactive V4.1 portfolio (−67.8%) are failed experiments, not candidates.

## What cannot be determined from the repository

- Current row counts in any forward ledger (V1, V4.1, E1, rule-based signals).
- Whether the daily workflow has run successfully since 2026-08-20.
- V1's historical or forward performance.
- Whether V5's validator has ever been run on real data.
- Whether gates and parameters were truly fixed before results were seen.
- Whether the result files match what the validators would produce today.
- Actual NEPSE trading costs relative to the 1.00% assumption.
