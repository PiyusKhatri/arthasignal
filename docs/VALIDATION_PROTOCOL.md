# Forward Signal Validation Protocol

**Protocol version:** `2026-08-20-v1`  
**Forward evidence starts:** 2026-08-20  
**Status:** private validation only; not authorization for a public or paid signal launch

This document is the authoritative operational description of ArthaSignal's current forward paper-trade gate. If older planning notes conflict with this file or `src/pipeline/signal_validation_policy.py`, this versioned protocol controls future launch-gate evidence.

## Why the protocol is frozen

A forward test only has evidentiary value if the rules are fixed before the outcomes are observed. For that reason, signal scope, horizons, cost assumption, sample thresholds, independence treatment, and pass/fail criteria are versioned together.

Calls dated before 2026-08-20 may remain in the database as diagnostic history, but they are not counted as v1 launch-gate evidence.

## Signal scope

| Signal | Horizon | Minimum graded calls | Additional constraint |
| --- | ---: | ---: | --- |
| `rsi_14 < 30 (oversold)` | 20 trading days | 60 | none |
| `close < bollinger_lower` | 20 trading days | 100 | none |
| `rsi_14 > 70 (overbought)` | 20 trading days | 60 | none |
| `doji` | 20 trading days | 60 | high-liquidity symbols only |

The higher Bollinger minimum reflects its historically higher firing frequency. If the required sample is not reached during the initially expected window, the validation period extends; the threshold must not be lowered after outcomes are observed.

## Call creation

- Signal conditions are sourced from the same `build_signal_conditions()` function used by the backtest pipeline.
- A continuous condition is de-duplicated into one episode: a `false -> true` transition creates a call; remaining true for multiple days does not create duplicate calls.
- Entry price is the **next trading session's open**, not the signal-day close.
- The `signal_logic_commit_hash` database field now stores a 40-character SHA-1 fingerprint of the source of `build_signal_conditions()`. Despite the historical column name, this is deliberately a signal-logic fingerprint rather than the repository HEAD. Unrelated UI/docs commits therefore do not split the validation population.
- If more than one signal-logic fingerprint appears in a signal's v1 population, that signal's gate becomes `invalid_logic_change`. A changed signal definition requires a new protocol version/start date rather than silently combining old and new logic.

## Resolution and data-quality rules

- The 20-day horizon is counted in trading sessions starting with the entry session.
- The target resolution price is the close on the target trading day.
- If the ticker has no trade on that target day, use the next available close, capped at **+3 trading days**.
- A call stays pending until that grace window has actually elapsed. It is not prematurely voided just because the target date has passed.
- If no price exists by the end of the grace window, mark the call `VOID` and exclude it from the performance gate.
- If a corporate action occurs between entry and resolution, mark the call `VOID` rather than grading an unadjusted raw-price move that may be mechanically distorted.
- Historical/as-of grading must never read prices later than the supplied `as_of` date.

## Transaction-cost assumption

v1 subtracts **50 basis points (0.50%) round-trip** from each resolved call when evaluating the launch gate.

This is a pre-committed research assumption based on the working 0.4-0.5% planning estimate. It is not a claim that every actual NEPSE trade costs exactly 0.50%. If verified brokerage, spread, tax, or other execution costs materially change the appropriate assumption, create a new protocol version before collecting evidence under the new rule.

## Cross-sectional dependence

Calls on many stocks on the same trading day are not treated as independent observations. A broad NEPSE move can affect them together.

For each signal:

1. calculate each resolved call's net return after the 50 bps cost assumption;
2. group calls by `entry_date`;
3. compute the mean net return for each entry-date cluster;
4. compute the fraction of calls in each entry-date cluster with positive after-fee return;
5. calculate 95% confidence intervals across those entry-date cluster statistics.

The gate requires at least **20 independent entry-date clusters** in addition to the per-signal minimum call count.

The current report uses a normal-approximation 95% interval over cluster-level observations. This is materially better than pretending every same-day ticker call is independent, but it remains a deliberately simple private-validation statistic rather than a publication-grade econometric claim. A more sophisticated bootstrap or market-factor model should be reviewed before presenting inferential statistics publicly.

## Pass/fail rule

A signal is `collecting` until it has both:

- its minimum graded-call count; and
- at least 20 independent entry-date clusters.

After those conditions are met, it passes only when all of the following are true:

- exactly one signal-logic fingerprint exists in the scoped population;
- the **lower bound of the 95% CI for clustered mean net return is > 0%**; and
- the **lower bound of the 95% CI for clustered after-fee win rate is > 50%**.

If the sample is ready but either confidence-bound condition is not met, the signal is `fail`. The overall v1 result passes only if every scoped signal passes.

This is intentionally stricter than reporting a raw win percentage above 50%.

## Checking progress

Human-readable:

```bash
python -m src.pipeline.validation_status
```

JSON:

```bash
python -m src.pipeline.validation_status --json
```

The report includes graded calls, independent entry days, pending/void counts, signal-logic fingerprints, clustered net return, clustered after-fee win rate, confidence bounds, and gate status.

## Correction log

**2026-09-30: session counting.** The rule "the 20-day horizon is counted in trading sessions" was implemented against `trading_calendar`, which counted Sundays after NEPSE moved to a Monday to Friday week (2026-04-06) and unmarked holidays as sessions. Calls were therefore graded early; calls signalled on 2026-08-20 resolved after 16 real sessions.

The rule is unchanged. The implementation now counts only dates that have rows in `daily_prices`. 1,046 graded calls were reset and graded again with `python -m src.pipeline.regrade_signal_calls`; 703 resolved and 343 returned to pending. Before and after figures are in `docs/DATA_READINESS.md`. No threshold, signal, horizon or cost assumption was changed, so the protocol version stays `2026-08-20-v1`.

## Change control

The following changes require a new protocol version and a new forward evidence start date:

- adding/removing/changing a signal definition;
- changing a horizon;
- changing liquidity restrictions;
- changing the execution price convention;
- changing the transaction-cost assumption;
- changing minimum sample or independent-day requirements;
- changing the clustering/statistical method in a way that changes the gate;
- changing pass/fail thresholds.

Do not make these changes retroactively to rescue an observed result.

## Public-launch boundary

Passing this statistical gate does **not** by itself authorize a public or paid signal product. The separate project blocker remains: obtain advice from an actual Nepal securities lawyer on applicable SEBON/investment-advisory requirements before Phase 2 public signal publishing or monetization.
