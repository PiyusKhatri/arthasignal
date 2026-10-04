# Pre-registration: meta-labeler on the ranker's calls (m1)

**Declared 2026-10-04T11:57:50+05:45** (Nepal time) in a separate commit after `docs/RANKER_PREREG.md` and **before any ranker prediction, call or grade existed**, so neither design was chosen with knowledge of the other's results. Constants: `src/ranker/meta_spec.py`. Registered in `backtest_variant_trials` (family `meta_prereg_v1`, 4 rows) and in `scorecard_accuracy_v2`.

## Idea (López de Prado's meta-labeling)

The primary model (ranker r1 at horizon *h*) decides **what** to buy. A secondary model decides **whether to act** on each primary call. It is trained only on past primary calls and their known outcomes, and it keeps only the calls it rates most likely to be correct. It states a probability, so the v2.1 calibration gate applies to it in full.

## Specification

- **Primary calls:** the out-of-sample `ranker_lgbm_h{h}` r1 calls (10 per test session).
- **Label:** whether the primary call is correct under protocol v2.1 at horizon *h* (the scorecard grade; unfilled, blocked and stranded calls are wrong; data-error windows excluded).
- **Features, all known at the close of *t*:**
  - `score`: the ranker's raw prediction;
  - `score_rank`: its within-date percentile among eligible stocks;
  - `gap_to_11th`: the call's prediction minus the 11th-best eligible prediction;
  - `pick_rank`: 1 to 10;
  - `state_code`, `breadth_sma50`, `nepse_ret_20`;
  - the stock's within-date percentile of `vol_20`, `turnover_20` and `ret_20`;
  - `e2_bonus_20` and `e4_streak_20`.
- **Model:** logistic regression (scikit-learn, L2, C = 1.0, lbfgs, features standardized on the training set). There is no hyperparameter grid; there is one trial per horizon.
- **Walk-forward:** meta test folds are the ranker's folds 2019, 2020, 2021, 2022, 2023 and 2024-01-01 to 2025-01-19. There is no meta fold in 2018, because no earlier primary calls exist.
  - Training data for a meta fold: primary calls from earlier test folds whose label exit index is before the meta fold's first session − 5.
  - A fold with fewer than 300 such calls makes no meta calls.
- **Keep rule:** keep a primary call when its predicted probability is ≥ τ, where τ is the 70th percentile of the fitted probabilities on that fold's training calls. That keeps roughly the top 30%.
- **Calls:** each kept call is written as a `replay` call `meta_lgbm_h{h}` version `m1`, with `probability` = the predicted probability.

## Evaluation (decides PASS)

The same rule as for the ranker:

- The primary cell is horizon *h*, situation `all`, under every v2.1 gate condition, with *K* taken from the registry at evaluation time.
- This model **states probabilities**, so calibration must also pass: ECE ≤ 0.05, |mean stated − observed| ≤ 0.05, and Brier ≤ the baseline-forecast Brier.
- A look-ahead check confirms that each meta fold's training calls all exit before the fold starts (minus the embargo).
- A replay PASS is not a live claim.
