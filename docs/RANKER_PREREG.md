# Pre-registration: cross-sectional LightGBM ranker (r1)

**Declared 2026-10-04T11:57:43+05:45** (Nepal time), before any feature matrix, label or model output was computed. The commit time of this file is the declaration time; `src/ranker/spec.py` holds the same constants in code (`DECLARED_AT`), and both are registered in `backtest_variant_trials` (family `ranker_prereg_v1`, one row per trial, and also in `scorecard_accuracy_v2` so the trials raise *K* for every gate).

**Protocol:** `accuracy-v2.1` (grading unchanged from v2). **Data:** the development window 2014-06-01 to 2025-01-19 only, read through the research role; the holdout (2025-09-30 onward) is not touched; the floorsheet Parquet files are read only through the existing derived feature store.

## What is being tested

One model per horizon *h* ∈ {5, 10, 20, 40}. Each model ranks the eligible equities at the close of every test session; the top 10 become buy calls graded by the scorecard. The question is whether any of the four models passes the v2.1 gate at its own horizon.

## Universe and calls

- Universe: `companies.instrument_type = 'Equity'` (promoter shares excluded since v2.1), valid symbols, traded on day *t*.
- **Eligible for a call** (same rules as the paper bots, `src/league/bots.py` `League.eligible`): traded on ≥ 50 of the last 60 sessions, 60-session median turnover ≥ Rs 2,000,000, no unresolved price step in the last 120 sessions, did not close at the upper circuit limit on *t*.
- **Calls:** the 10 eligible stocks with the highest predicted score, at most 3 per sector, every test session, no probability stated. No other selectivity in r1 (selectivity is the job of the separately pre-registered meta-labeler, `docs/META_PREREG.md`).

## Features (all known at the close of *t*)

Stock-level features are converted to **cross-sectional percentile ranks within each date** (0 to 1, ties averaged, missing stays missing) before training; market-level features are used raw. Returns are total returns (`panel.total_return`, corporate actions applied); windows with corrupt data use the stored values as they are.

| Group | Features |
| --- | --- |
| Price, volume, volatility, trend | `ret_1`, `ret_5`, `ret_20`, `ret_60` (compounded total return); `mom_120_skip5` (return from *t*−119 to *t*−5); `vol_20`, `vol_60` (std of daily total returns); `turnover_20` (log of 20-session median turnover); `turnover_ratio_5_60` (5-session mean ÷ 60-session mean turnover); `close_sma20`, `close_sma50`, `close_sma200` (close ÷ moving average − 1); `dist_high_240` (close ÷ 240-session max close − 1); `upper_hits_20`, `lower_hits_20` (upper/lower circuit closes in the last 20 sessions); `traded_share_60`; `age_sessions` (sessions since first trade, capped at 250); `log_price` |
| Sector-relative | `ret_20_vs_sector`, `ret_60_vs_sector` (stock − same-date sector median); `vol_20_vs_sector`; `sector_ret_20` (sector median `ret_20`) |
| Corporate-action proximity (past only) | `since_bonus`, `since_right`, `since_dividend` (sessions since the last ex-session of that action type, capped at 250); `e2_bonus_20`, `e4_streak_20` (the E2/E4 avoid flags); `since_dividend_declaration` (sessions since the latest dividend declaration *announcement*, capped); `declared_bonus_pct` (bonus % of that declaration). Book-close dates of pending declarations are **not** used (they may have been filled in later) |
| Floorsheet broker features | `broker_h1` … `broker_h5` from the existing feature store `derived/broker_flow/features.parquet` (the five pre-registered broker-flow features, computed at the close of *t*; missing where the store has no value) |
| Earnings (publication-dated) | From Sharesansar quarterly announcements, known from the later of the Sharesansar and Merolagani publication dates (as in `info_eval`): `profit_growth_yoy` (same quarter, prior fiscal year, only if that report was also known), `since_report` (sessions since the latest report became known, capped), `profit_positive`, `report_count` (reports known so far, capped at 40) |
| Market state | `nepse_ret_20`, `nepse_ret_60`; `breadth_sma50` (share of traded equities closing above their 50-session average); `market_turnover_ratio_5_60`; `state_code` (bear 0, sideways 1, bull 2, overheated 3, from the scorecard's market-state labels); `rate_rising`, `rate_falling` (scorecard situation flags from T-bill rates by availability date) |

## Target

For horizon *h*: the scorecard's own grading cube (`build_cube`) gives each (stock, *t*) the gross return with **next-open entry** (close before 2018-02-18), exit at the open after *h* sessions, and realistic fills. Target = gross return − same-date universe mean (`cube.universe_mean`), converted to its **within-date percentile rank**. Rows that are unfilled, blocked, stranded, data errors or not yet matured are not used for training (they are still graded as calls).

## Walk-forward, purge and embargo

- **Test folds** (calendar blocks): 2018-02-18 to 2018-12-31, then 2019, 2020, 2021, 2022, 2023, and 2024-01-01 to 2025-01-19. Seven folds; every test session gets predictions from a model trained only on earlier data.
- **Training set for a fold:** every (stock, *t*) with *t* ≥ the 240th session (warm-up) whose label **exit index is < the fold's first session index − 5** (purge: no label window overlaps the test period; embargo: 5 more sessions). Expanding window.
- **Hyperparameter grid (fixed, 4 points per horizon, 16 trials total):** `num_leaves` ∈ {15, 63} × `min_child_samples` ∈ {200, 1000}; fixed: learning rate 0.03, 400 trees, row subsample 0.8 every iteration, column subsample 0.8, L2 1.0, seed 2026.
- **Selection inside each fold, without touching the test fold:** the last 20% of the fold's training dates is an inner validation block, with the same purge and embargo between inner-train and inner-validation; each grid point is fitted on inner-train and scored by the **mean daily Spearman rank correlation** between prediction and target on inner validation; the best grid point is refitted on the full training set and used for the test fold. All 16 trials are counted in the registry whether or not they are selected.
- Dates with fewer than 30 labelled stocks are skipped for training and IC.

## Evaluation (the only thing that decides PASS)

- Calls are written to the ledger as `replay` calls (strategy `ranker_lgbm_h{h}`, version `r1`) and graded by `grade_calls_v2` at all eight horizons.
- **Primary cell per model:** horizon = *h*, situation `all`. PASS needs every v2.1 gate condition: edge ≥ +8 points; plain and penalized lower bounds > 0 with *K* = versions in `scorecard_accuracy_v2` × 104 at evaluation time; expectancy > 0 at 0.5/1.0/1.5% and excess expectancy > 0 at 1%; edge > 0 in ≥ 3 of 4 time folds; sample minimums; calibration (no probability stated → "uncalibrated"); look-ahead audit passed.
- **Look-ahead audit for r1:** at 20 randomly chosen test dates (seed 20261004), the full feature row of every eligible stock is recomputed from inputs truncated at that date (prices, actions, declarations, reports, index, rates) and must equal the full-history computation; broker features are checked to come from the store row dated *t*. Any mismatch marks the model leaky and it cannot pass.
- **A replay PASS would still not be a live claim.** Every other cell, the per-fold rank IC, top-decile excess returns and feature importance are reported as description only.

## What happens next, fixed now

- If no model passes: report NO EVIDENCE / INSUFFICIENT SAMPLE plainly; the meta-labeler step still runs as pre-registered.
- A model is registered as a challenger bot in the paper league **only if it is ready to run live**: the frozen artifact, every feature computable from live data before the call deadline, and a tested live writer. Passing or failing the replay gate does not change that rule; readiness does.
