# V5 Frozen Research Challenger Protocol

**Protocol / score-policy version:** `2026-08-21-v5-research-v1`  
**Model version:** `artha-pit-stability-v5-research-v1`  
**Feature-contract version:** `nepse-v5-pit-stability-v1`  
**Status:** frozen research challenger; no historical V5 outcome has been admitted under this protocol or its pre-outcome review corrections

## Purpose and boundary

V5 asks whether a daily, point-in-time, execution-aware, temporally stable model can improve the accuracy and reliability of stock selection beyond both the frozen V4.1 prediction challenger and its transparent momentum baseline.

V5 is not the live champion, is not an automatic successor to V4.1, and is not an execution-policy experiment. V1 remains the live champion. V4.1 and E1 remain frozen and continue collecting their own forward evidence independently.

This document freezes the V5 dataset, labels, features, models, score, comparisons, and gates before historical V5 outcomes are run. Historical results may reject this version; they may not be used to tune it in place.

## Separate versioned artifacts

The three identifiers above control different things and must be stored separately in every result or artifact:

- `nepse-v5-pit-stability-v1` controls row eligibility, point-in-time feature construction, labels, and feature order.
- `artha-pit-stability-v5-research-v1` controls the three fitted model heads, their parameters, their calibrators, and the resulting artifact fingerprint.
- `2026-08-21-v5-research-v1` controls score composition, temporal smoothing, selection, comparisons, historical gates, and forward gates.

A deployment snapshot, if V5 first passes the historical gate, must additionally have an immutable SHA-256 fingerprint over the serialized heads, calibrators, ordered feature names, fixed scales, parameters, training cutoff, and all three version identifiers. Two artifacts with different fingerprints are not the same challenger even when their display names match.

## Point-in-time daily dataset contract

### Observation and universe

One research opportunity is formed after a completed NEPSE market session **D**. It may use only data whose effective timestamp is on or before D's close.

V5 uses a complete market-session calendar rather than the legacy five-stock-observation sampling grid. On D, a stock must:

1. be an active NEPSE equity;
2. have an exact recorded trade and close on D;
3. have enough as-of-D history for every frozen V4.1 base feature;
4. be in the point-in-time medium or high same-date liquidity tercile under the frozen V4.1 investability rule; and
5. pass the frozen V4.1 outcome-free market, sector, and baseline candidate-pool construction.

Delisting knowledge, future trade availability, future horizon slippage, future liquidity, future corporate actions, and future regime labels are forbidden eligibility inputs. A date with no eligible stock is a successful abstention, not a missing run.

The research row builder must persist or hash the market-session calendar, source-price cutoff, eligible symbols, candidate symbols, ordered features, and label-resolution status. A reconstructed row whose point-in-time fingerprint differs from the originally frozen row is a different observation and cannot silently replace it.

### Signal, entry, horizon, and label

The precommitted timing convention is:

```text
completed session D close
  -> freeze features and score
  -> next NEPSE market session open
  -> twentieth NEPSE market session after D close
  -> at most three additional NEPSE sessions of resolution grace
```

The first NEPSE market session after D is the intended entry session **E**. A valid label requires an exact recorded stock open on E; entry is not moved forward to a later stock trade. A missing or invalid entry open makes the row ungradable, preventing future trade availability from selecting a convenient entry.

The nominal resolution session **T** is the twentieth completed NEPSE market session after D. The exit price is the stock's exact close on T. If it has no exact close on T, resolution may use the first exact stock close on T+1, T+2, or T+3. If no such close exists, the row is void. The NEPSE benchmark is measured over the identical entry and actual resolution timestamps.

The frozen targets are:

```text
stock_return = stock_close_at_resolution / stock_open_at_E - 1
nepse_return = nepse_close_at_resolution / nepse_open_at_E - 1
gross_excess_return = stock_return - nepse_return
net_alpha = gross_excess_return - 1.00% round-trip cost
success_after_cost = net_alpha > 0
severe_MAE = close-path MAE from stock_open_at_E <= -5.00%
```

MAE uses the point-in-time sequence of closes from E through the actual resolution session, relative to the entry open. Non-trading sessions may carry the last exact close for valuation but may not invent a trade or a new price.

Any split, bonus, rights adjustment, merger adjustment, or other corporate action with an effective date in the raw-price grading window from E through resolution voids the row. Corporate-action rows are never adjusted differently after their outcomes are seen. A void row is reported but is excluded symmetrically from V5, V4.1, and baseline matched statistics.

Unresolved and void rows may never enter training, calibration, test metrics, or gate counts.

## Frozen feature contract

V5 retains the frozen V4.1 point-in-time base and meta features, including the fixed multifactor baseline score and percentile and the as-of-D market, sector, and liquidity context. Their definitions and fixed ex-ante scaling may not change under this version.

Only the following five additional stability features are allowed:

1. **Trailing-20 stock trade-availability ratio:** the fraction of the 20 NEPSE market sessions ending on D with an exact stock trade.
2. **Trailing-20 turnover coefficient of variation:** population standard deviation divided by the mean of daily turnover over those same 20 market sessions, with zero turnover on a no-trade session. A zero mean makes the current row ineligible.
3. **Same-date trailing-turnover percentile:** the average-rank percentile, with ties sharing the same percentile, of the as-of-D trailing-20 mean turnover among active, base-data-qualified equities before the medium/high-liquidity exclusion.
4. **Prior-five baseline-percentile median:** the median of the stock's baseline percentiles over the five completed NEPSE sessions immediately before D. A prior session on which the stock has no valid baseline rank contributes no percentile; if none of the five has a value, the fixed neutral value is `0.50`.
5. **Prior-five candidate-membership count:** an integer from `0` to `5`, counting how many of those same five sessions placed the stock in the frozen baseline candidate pool. Missing or ineligible prior sessions count as zero membership.

The stability windows are market-session windows, not stock-observation windows. The current D value is allowed only in the trailing-20 features; D is forbidden from both prior-five features.

The fixed model inputs are scaled as follows: the two ratios and two percentiles remain on `[0, 1]`; turnover coefficient of variation is clipped to `[0, 5]` and divided by `5`; candidate-membership count is divided by `5`. No test-period normalization, winsorization, target encoding, missingness indicator, fundamental field, news field, sentiment field, or later-discovered feature may be added.

## Frozen model heads

All heads use the identical ordered V5 feature vector and only matured training labels.

### 1. Cross-sectional net-alpha ranker

The first head is a LambdaMART/XGBoost `rank:ndcg` model grouped by signal date. Its target ordering is realized `net_alpha`. Training dates with fewer than ten eligible candidates are excluded from ranker fitting, but remain eligible for the two classifiers and for later evaluation when all strategies can be compared at exact breadth.

The ranker's raw outputs are converted on each evaluation date to average-rank percentiles on `[0, 1]`; tied raw scores receive the same average percentile. Test labels never participate in that conversion.

The frozen ranker parameters are 180 boosting rounds, `ndcg@10`, maximum depth 4, learning rate `0.035`, minimum child weight 10, row subsample `0.80`, column subsample `0.80`, L2 `3.0`, L1 `0.30`, histogram trees, seed `20260821`, and one training thread.

### 2. Success-after-cost classifier

The second head is a binary XGBoost classifier for `gross_excess_return > 1.00%`, equivalently `net_alpha > 0`. Its raw probability is Platt-calibrated only on the purged calibration segment.

### 3. Severe-MAE classifier

The third head is a binary XGBoost classifier for a close-path MAE of at least `5.00%` in magnitude. Its raw probability is independently Platt-calibrated only on the purged calibration segment. Score composition uses its complement, `1 - P(severe MAE)`, as downside safety.

Both classifiers use 220 boosting rounds, binary log loss, maximum depth 4, learning rate `0.035`, minimum child weight 8, row subsample `0.78`, column subsample `0.78`, L2 `2.5`, L1 `0.25`, maximum delta step 1, histogram trees, seed `20260821`, and one training thread. Each Platt map uses 500 iterations, learning rate `0.035`, and L2 `0.02`.

If a head or calibrator lacks the frozen minimum sample or class support required by the implementation, that fold is invalid. An uncalibrated fallback is not admissible.

## Fixed score and temporal stability

No score weight or threshold is fitted from outcomes.

For stock i on date D:

```text
current_composite(i, D)
  = 0.50 * calibrated_P(success_after_cost)
  + 0.30 * within_date_rank_percentile
  + 0.20 * (1 - calibrated_P(severe_MAE))
```

The stored decision score is:

```text
final_score(i, D)
  = 0.70 * current_composite(i, D)
  + 0.30 * median(prior valid current_composites for i over D-5 ... D-1)
```

The prior-score median may use only valid V5 `current_composite` values from the five completed NEPSE market sessions strictly before D. It may use one to five available values. If none exists, `final_score` equals the current composite. The current date, later dates, reconstructed future outcomes, and carried future model scores are forbidden from the history term.

V5 ranks candidates by descending final score. Ties resolve by higher current composite, then higher frozen baseline percentile, then symbol in ascending lexical order. These tie rules are part of the policy.

## Immutable, purged historical evaluation

Historical evaluation uses exactly four expanding chronological outer folds. Each fold has separate training, calibration, and test date segments.

- A training row is retained only when its actual resolution date, including grace, is strictly before calibration starts.
- A calibration row is retained only when its actual resolution date, including grace, is strictly before test starts.
- Each signal date appears in at most one outer test fold.
- A stock or date may appear in earlier training and later test periods; no observation, label window, fitted transform, calibrator, score history, or early-stopping decision may cross a boundary.
- The baseline expectation table, model heads, all learned parameters, and climatology are fitted from the fold's training rows only. Platt calibration is fitted from that fold's calibration rows only.
- Prior-five features and prior-five score smoothing are reconstructed chronologically within each fold. Test scoring never seeds history with a score fitted using a later test date.
- Hyperparameters, feature order, score weights, breadth rules, gates, bootstrap seed, and fold boundaries are fixed before any outer-test metric is inspected.

The complete split manifest must be hashed before model evaluation. It includes every signal date, actual label-end date, fold assignment, exclusion reason, and row fingerprint. A changed manifest invalidates the earlier result rather than updating it in place.

## Exact-breadth comparisons

V5 has two required comparators:

1. the architecture-locked V4.1 aligned reference, fitted independently inside each V5 purged fold with V4.1's frozen heads, calibration, thresholds, bounded score correction, and selection policy; and
2. the transparent fixed multifactor momentum baseline, ranked by its frozen baseline percentile.

The historical V4.1 reference is deliberately not the deployed frozen snapshot. That snapshot was trained after some historical test dates and would leak future training information if replayed backward. It is also not the old V4.1 research output, whose twentieth-stock-observation labels are not comparable to V5's next-open/twentieth-NEPSE-session target. The aligned reference is genuine out-of-sample evidence under the same purged folds and labels, while keeping V4.1's architecture and policy fixed. It never writes to, updates, or reinterprets any V4.1 artifact, table, or forward ledger. Forward evidence continues to use the actual frozen V4.1 ledger.

No comparator may see V5's test outcomes. For probability accuracy only, a copy of the V4.1 probability output may be recalibrated to V5's `success_after_cost` target using that fold's calibration rows. This creates a same-target diagnostic comparator; it does not change V4.1 ranking, selection, model artifacts, stored predictions, or forward ledger.

On each comparison date, all three strategies receive the identical point-in-time candidate set and identical actual resolution rules. Corporate-action, missing-entry, missing-exit, and other void rows are removed from this common candidate matrix before any fold fitting, scoring, or selection; no strategy gets an ordinal replacement after seeing another strategy's selection. Let `k_D` be the number of valid selections made by the aligned V4.1 policy on D. V5 selects its top `k_D` final scores, V4.1 keeps its selected `k_D`, and the baseline selects its top `k_D` percentiles. Dates with `k_D = 0` remain recorded as V4.1 abstentions but contribute no matched call.

Every alpha, risk, calibration, and stability comparison uses only triple-matched dates and exactly `k_D` resolved, non-void calls per strategy. Results must report V5-minus-V4.1 and V5-minus-baseline separately. The report must also show selected counts, dates, voids, abstentions, and exclusions so breadth cannot be hidden.

Brier skill is `1 - (V5 Brier / climatology Brier)`, where the climatology is the success rate from that fold's training rows and is applied unchanged to its test rows. Expected calibration error uses ten fixed-width probability bins, `[0.0, 0.1)` through `[0.9, 1.0]`, and is the call-count-weighted mean absolute difference between each non-empty bin's mean probability and observed success rate.

Paired uncertainty uses 1,000 deterministic resamples with seed `20260821`. The date moving-block bootstrap uses contiguous 20-NEPSE-session blocks. The independent-cohort analysis groups signal dates into non-overlapping 20-session cohorts and resamples cohorts rather than individual stocks.

## Historical admissibility gate

V5 remains `research_rejected` unless every item below is true:

- exactly **4 valid purged outer folds** complete;
- mean selected net alpha after 1% cost is positive in at least **3 of 4** folds;
- V5 beats the architecture-locked aligned V4.1 reference at exact breadth in at least **3 of 4** folds;
- aggregate V5-minus-V4.1 mean **and** median net alpha are both greater than zero;
- the lower 95% bound of the 20-session date moving-block V5-minus-V4.1 interval is greater than zero;
- the lower 95% bound of the non-overlapping 20-session cohort V5-minus-V4.1 interval is greater than zero;
- selected V5 median net excess after the 1% cost is greater than zero;
- V5 severe-drawdown rate and mean MAE are each no worse than V4.1 at exact breadth;
- success-probability Brier skill is greater than zero versus the fold's training-only climatology;
- success-probability expected calibration error is at most **0.05**;
- V5 success-probability Brier score is no worse than the same-target recalibrated V4.1 probability in at least **3 of 4** folds;
- median consecutive-active-date selected-set Jaccard similarity is at least **0.50**;
- the next-open same-mechanics V5 replay has positive compounded return and beats the V4.1 replay;
- the V5 replay has annualized turnover at most **30x**, mean completed holding period at least **5 sessions**, and maximum drawdown no worse than V4.1; and
- the exact-breadth historical sample contains at least **100 selected V5 rows** across at least **50 active dates**.

The baseline receives every exact-breadth alpha, risk, bootstrap, calibration-where-applicable, stability, and replay report beside V5 and V4.1 even though the stricter promotion hurdle is V5's incremental result over V4.1. A missing baseline comparison invalidates the report.

The next-open same-mechanics replay applies the frozen E1 v1 mechanics symmetrically: maximum five positions, 20-session holding cap, 1.00% round-trip cost, no routine rebalancing, 0.12 replacement margin, no leverage, no shorting, and no invented execution on a missing open. This is a V5 research diagnostic. It does not edit E1 or create E2.

For the replay adapter only, an already-selected V5 row is marked entry-eligible so E1 can consume its frozen rank; the adapter cannot promote an unselected row, alter its score, use an outcome, or change breadth. V4.1 and the baseline use their own selected rows under the same next-open calendar and price histories.

A historical `pass` permits one frozen V5 forward artifact and a new V5-only append ledger. It does not promote V5 or alter any production workflow.

## Forward evidence gate

Forward collection may begin only after a historical pass and snapshot freeze. Every completed market session must append an immutable V5 heartbeat as `captured`, `abstained`, or `failed`. Captured rows must include the source-data cutoff, feature and model versions, artifact fingerprint, candidate-universe fingerprint, all three head outputs, current composite, the exact prior-score fingerprints used in smoothing, final score, comparator scores, and a row fingerprint.

No forward prediction may be backfilled after any part of its target window is observed. Resolution may fill only predeclared outcome fields. A correction to a captured input or prediction creates a new failed/corrected audit event under a new version; it never overwrites accepted evidence.

The forward gate remains `collecting` until it has all of:

- at least **120 successful signal dates**, including valid abstentions;
- at least **80 triple-matched resolved V5/V4.1/baseline calls**;
- at least **20 matched active dates**; and
- at least **12 non-overlapping 20-session cohorts**.

After maturity, V5 receives a forward `pass` only when every condition below is true:

- there are **zero missing V5 captures** for expected signal dates;
- mean and median V5-minus-V4.1 net alpha are both greater than zero;
- the positive V5-minus-V4.1 incremental-date rate is greater than **50%**;
- the lower 95% bounds of both the date bootstrap and the non-overlapping cohort bootstrap are greater than zero;
- V5 severe-drawdown rate and mean MAE are each no worse than V4.1;
- success-probability Brier skill is greater than zero versus the frozen training climatology;
- success-probability expected calibration error is at most **0.05**;
- the next-open V5 replay has positive compounded return and beats the same-mechanics V4.1 replay;
- V5 replay annualized turnover is at most **30x**;
- mean completed V5 holding period is at least **5 sessions**; and
- V5 replay maximum drawdown is no worse than V4.1.

Triple-matched V5-minus-baseline results are mandatory companion evidence under the identical dates, breadth, labels, and bootstrap units. Successful abstentions count toward operational continuity but not resolved-call or active-date minima. Corporate-action voids count neither as failures nor resolved calls, and must void the corresponding matched rows on all three sides.

A forward `pass` earns manual champion review only. It never triggers automatic promotion, trading, public signals, or replacement of V1, V4.1, or E1.

## Pre-outcome review corrections

Before the first V5 historical outcome run, independent code review found and corrected four implementation ambiguities: score history is bounded to the actual preceding five NEPSE sessions; lagged baseline percentiles come from the full eligible rank history rather than candidate-only history; the matched baseline explicitly sorts by percentile; and the required next-open replay is fully wired. This section also makes the aligned historical V4.1 comparator definition explicit. These corrections consumed no V5 outcome metric and therefore complete, rather than revise after evidence, the initial protocol freeze.

## Change control

After the first historical V5 outcome is inspected, none of the following may change under these version identifiers:

- daily universe or candidate construction;
- entry, horizon, grace, transaction-cost, MAE, or corporate-action rules;
- feature definitions, scales, windows, missing-value rules, or feature order;
- model heads, targets, parameters, seeds, calibration method, or minimum support;
- composite weights, temporal weights, score-history rules, tie breaks, or breadth;
- split boundaries, purge rules, bootstrap units, metrics, or gate thresholds; or
- replay mechanics or comparator definitions.

A material change requires a new model version, feature version when applicable, policy/protocol version, artifact fingerprint, split manifest, historical result, and forward ledger. Results from different versions may be shown side by side but may not be pooled to satisfy a gate.

A code correction that changes any historical score, selection, label, or comparator invalidates all V5 results produced by the affected version. Pure formatting or reporting corrections may retain evidence only when row fingerprints and all metric inputs remain identical.

## Frozen-system protection

V5 is additive research only.

- Do not retrain, rewrite, delete, or reinterpret the frozen V4.1 artifact or its prediction ledger.
- Do not rewrite, delete, or change the frozen E1 decisions, portfolio mechanics, or forward ledger.
- Do not insert V5 outputs into V4.1 or E1 tables.
- Do not treat the V5 replay as E1 historical evidence.
- Do not create E2 from V5 research.
- Do not alter the live V1 champion or production workflow without a later explicit manual decision.

The V4.1 and E1 daily ledgers remain the primary uninterrupted forward evidence streams while V5 is evaluated separately.
