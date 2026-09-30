# Backtest and Validation Plan

Status: design and skeleton only. No model has been trained or evaluated with this framework.

Every future model must be built and judged through `src/backtest/`. The rules below exist because the first round of research (see `docs/HONEST_STATUS.md`) had no untouched holdout, entered at the signal-day close, was tuned five times against one test window, and never beat buy-and-hold NEPSE.

## The rules

| # | Rule | Enforced by |
| --- | --- | --- |
| 1 | The last 12 months and everything after are a locked holdout | `holdout_config.json`, `holdout.py` |
| 2 | Only `final_evaluation()` may read holdout labels, and every call is logged | `holdout.py`, `ledger.py`, table `backtest_holdout_evaluations` |
| 3 | Development uses walk-forward folds with purge and embargo | `splits.py` |
| 4 | Entry is the next session's open, never the signal-day close | `entry.py` |
| 5 | Three baselines are always reported | `baselines.py`, `report.py` |
| 6 | Costs are reported at 0.5%, 1.0% and 1.5% round trip | `costs.py`, `report.py` |
| 7 | Sells that cannot execute are tracked, not assumed away | `entry.py`, `report.py` |
| 8 | Confidence intervals are clustered by signal date | `stats.py` |
| 9 | Every model variant tried is counted and widens the intervals | `ledger.py`, `stats.py`, table `backtest_variant_trials` |
| 10 | The universe includes delisted symbols and reports how many have prices | `universe.py` |

## 1. Locked holdout

`src/backtest/holdout_config.json`:

| Field | Value |
| --- | --- |
| `version` | `2026-09-30-holdout-v1` |
| `holdout_start` | 2025-09-30 |
| `holdout_end` | none (open-ended: all later data is holdout) |
| `horizon_sessions` | 20 |
| `grace_sessions` | 3 |
| `embargo_sessions` | 5 |
| `cost_levels_round_trip` | 0.005, 0.010, 0.015 |
| `base_alpha` | 0.05 |

- A row belongs to the holdout if its signal date is on or after `holdout_start`.
- A row signalled before the holdout whose label resolves inside it is a boundary row. It is dropped from development and is not part of the holdout evaluation.
- The config's SHA-256 is stored with every holdout evaluation, so a changed config is visible in the log.
- A test pins the version and start date. Moving the boundary means a new version, a failing test to update on purpose, and a commit that shows it.

**Caveat.** The 2025-09-30 to 2026-07-23 part of this window was inside the last test fold of the archived V2 to V5 research, so its aggregate behaviour has been seen. It is untouched for new models, not untouched in absolute terms. Data after 2026-07-23 has not been used by any model.

## 2. The guard and `final_evaluation()`

All labelled data must pass through `partition_rows(rows, config)`, which returns:

- `development`: rows that do not touch the holdout;
- `holdout`: a `SealedHoldout`;
- `boundary_dropped`: the count of boundary rows.

What the guard does:

- `assert_development_only(rows, config, purpose)` raises `HoldoutViolation` if any labelled row touches the holdout. `purpose` must be `training`, `feature_selection`, `tuning` or `walk_forward`.
- `@development_only(purpose, config)` wraps a function so the check runs before it. Every training, feature-selection and tuning entry point must carry it.
- `SealedHoldout` cannot be iterated, indexed or measured. `feature_rows()` returns rows with the label and benchmark return stripped, so a frozen model can score them. `labeled_rows()` raises unless called by `final_evaluation()`.

`final_evaluation(model_id, select, holdout, ledger, requested_by, reason)`:

1. Writes a `started` row to `backtest_holdout_evaluations` **before** any label is read. The row records model id, config version and hash, who asked, why, the call number for this config version, and how many variants had been tried.
2. Calls `select` with feature-only rows to get the model's picks.
3. Unseals the labels and builds the standard report.
4. Marks the row `completed` with the report, or `failed` if anything raised.

A second call gets call number 2, a third 3, and so on. Repeated peeking is on the record and cannot be undone by a crash.

**Limit.** Python cannot stop someone querying `daily_prices` directly and computing holdout returns by hand. The guard makes the honest path the easy one and makes the log authoritative: a holdout number that has no row in `backtest_holdout_evaluations` does not count.

## 3. Walk-forward splits

`walk_forward_folds(sessions, config, n_folds=4, min_train_sessions=252)`:

- Drops every session on or after `holdout_start` before doing anything else.
- Builds expanding folds: each fold trains on everything before its gap and tests on the next block.
- Leaves a gap between the last training session and the first test session of `horizon + grace + 1 + embargo` = 20 + 3 + 1 + 5 = **29 sessions**. The `+1` is the next-open entry lag.

`fold_rows(rows, fold, config)` then purges at row level: a training row is kept only if its label's actual exit date is before the fold's first test session. This matters for blocked sells, whose exit can be much later than 20 sessions.

Fold boundaries, fold count and minimum training length must be fixed before the first result is looked at.

## 4. Entry and exit prices

`next_open_label(market_sessions, bars, signal_date, horizon_sessions, grace_sessions)`:

| Step | Rule |
| --- | --- |
| Signal | Features use data up to the close of session D |
| Entry | The open of the next market session, D+1 |
| No open on D+1 | The row is ungradable. Entry is never moved to a later trade |
| Exit target | The close of the 20th market session after D |
| Unresolved | No label until the full grace window is observable |

Sessions are counted on the market calendar, not on the stock's own trading days. The benchmark return uses the index open on the entry session and the index close on the actual exit session.

`assert_entry_after_signal` raises `EntryRuleViolation` for any label whose entry date is not after its signal date. The report calls it on every row, so labels built elsewhere cannot bypass the rule.

## 5. Blocked sells

| Exit status | Meaning | Exit price |
| --- | --- | --- |
| `on_time` | Stock traded on the target session | That close |
| `delayed` | First trade within 3 sessions after target | That close |
| `blocked` | No trade within the grace window | The first later close, however long it takes |
| `stranded` | No trade again before the data ends | Last traded close, flagged as unconfirmed |

Blocked and stranded positions stay in the results. Every report states exit-status counts and the blocked-sell rate. The archived research voided these rows, which removes exactly the trades that illiquidity hurts.

## 6. Baselines

Every report compares the model with all three, on the same signal dates:

| Baseline | Definition | Cost charged |
| --- | --- | --- |
| NEPSE buy-and-hold | Index return over each selected call's entry-to-exit window | None |
| Equal-weight universe | Mean return of every gradable stock on that date | Same as the model |
| Simple momentum | Top names by momentum score, same breadth as the model that date | Same as the model |

The report gives each baseline's net return and the model-minus-baseline difference, both with clustered intervals.

## 7. Cost sensitivity

Results are always shown at 0.5%, 1.0% and 1.5% round trip. A model whose edge exists only at 0.5% has no edge worth trading. These are assumptions until real brokerage, fees, tax and spread are measured.

## 8. Statistics

- **Clustering.** Calls on the same signal date are averaged into one observation. The interval is a normal approximation over those date means. Fewer than two dates gives no interval.
- **Multiple testing.** Each distinct variant (model family plus parameter fingerprint) is registered with `register_variant`. The report uses `alpha = 0.05 / variants_tried` (Bonferroni), so every extra variant widens every interval. The count is never reset.
- **Known weakness.** Twenty-session windows on adjacent dates overlap, so date means are still correlated. Date clustering understates uncertainty. Before any claim is made, add a non-overlapping-cohort or block-bootstrap interval and report both.

## 9. Universe and survivorship

`load_universe` returns every equity in `companies` regardless of status. `survivorship_coverage` reports, by status, how many symbols are listed and how many have prices.

Current local figures (`docs/DATA_READINESS.md`): 312 of 312 active, 32 of 184 delisted, 3 of 25 suspended. A backtest on this data is survivor-biased and must say so until delisted history is loaded.

## Module map

| File | Contents |
| --- | --- |
| `holdout_config.json`, `config.py` | The locked holdout and its loader |
| `types.py` | `Bar`, `Label`, `Row`, exit-status constants |
| `holdout.py` | `assert_development_only`, `development_only`, `partition_rows`, `SealedHoldout`, `final_evaluation` |
| `ledger.py`, `models.py` | Variant counter and holdout-call log; in-memory and database versions |
| `splits.py` | `walk_forward_folds`, `fold_rows` |
| `entry.py` | `next_open_label`, `benchmark_return`, `assert_entry_after_signal` |
| `baselines.py` | The three baselines |
| `costs.py` | Cost levels and net return |
| `stats.py` | Clustered interval, multiple-testing alpha |
| `universe.py` | Universe loader and survivorship coverage |
| `report.py` | `selection_report`, the one standard report |

Tests: `tests/test_backtest_leakage_guard.py` (17 tests) and `tests/test_backtest_entry_price.py` (11 tests).

The two ledger tables are registered in `src/database/init_db.py`. Run `python -m src.database.init_db` on any database before using `DatabaseLedger`.

## Not built yet

- A loader that turns `daily_prices` and `market_index` into `Row` objects. It must return a `Partition`, never raw labelled rows.
- Feature construction, including any floorsheet features.
- A portfolio simulator with position limits and capital.
- A non-overlapping-cohort or block-bootstrap interval.
- A pre-registration record: gates written down and committed before the first development result.

## Blocked on data

The framework cannot give trustworthy results until the problems in `docs/DATA_READINESS.md` are fixed:

1. Session dates since 2026-08-21 are unreliable (no Sundays, three Fridays).
2. The trading calendar counts sessions that did not happen, which breaks the 20-session horizon.
3. Price history starts in mid-2021; after removing the 12-month holdout about four years remain for development.
4. Delisted symbols have almost no prices.

## Order of work

1. Fix the data problems above.
2. Build the row loader behind `partition_rows`.
3. Write down the gates for the first model and commit them.
4. Run the three baselines alone through walk-forward. If the framework cannot reproduce sensible baseline numbers, nothing built on it can be trusted.
5. Only then start modelling, registering every variant.
6. Call `final_evaluation()` once, for one frozen model.
