# Execution Policy E1

**Policy version:** `2026-08-21-execution-e1-v1`  
**Predictive model:** frozen V4.1 challenger (`artha-residual-alpha-v4.1-shadow-v1`)  
**Status:** historical execution research only; does not alter the live V1 champion or the V4.1 forward model

## Purpose

V4.1 showed repeatable stock-selection improvement over the transparent momentum baseline, but the first portfolio simulation exposed destructive turnover: roughly one- to two-session holding periods, very high annualized turnover, and transaction costs large enough to overwhelm the research alpha.

E1 tests a narrower question: **can the exact frozen V4.1 predictions be executed with materially less churn without changing the prediction model?**

No V4.1 feature, threshold, model weight, calibration rule, candidate rule, or forward-shadow prediction is changed by E1.

## Frozen execution rules

- Maximum simultaneous positions: **5**.
- Weighting: new/replacement positions target one-fifth of current portfolio wealth; surviving holdings are not routinely rebalanced back to equal weight.
- Maximum holding period: **20 NEPSE sessions**.
- Transaction-cost assumption: **1.00% round trip** (0.50% per side in the simulator).
- No leverage.
- No shorting.
- A trade requires an exact stock print on the execution session. Last-traded prices may be carried for mark-to-market valuation, not for ordinary execution.
- Terminal stale-price liquidation is allowed only as an explicitly reported final accounting approximation.

## Hold and exit policy

An existing V4.1 holding survives ordinary rank movement. A small change such as rank #2 to rank #4 is not, by itself, an exit.

A holding exits when one of the following happens:

1. a fresh observation shows the stock is no longer in the candidate set;
2. V4.1 explicitly classifies the candidate as `REJECT`;
3. the holding reaches 20 NEPSE sessions; or
4. a qualified challenger exceeds the weakest held candidate by the fixed replacement margin described below.

If an exit is required but the stock does not trade on that session, the position remains marked at the last traded price and the exit is retried when an executable print exists.

## New entries

V4.1 mode uses the frozen V4.1 entry qualification:

- the candidate is not `REJECT`;
- the market regime permits positions; and
- expected excess return is above the 1% execution hurdle, unless V4.1 gives the stronger `PROMOTE` action.

The transparent momentum comparator uses the same E1 portfolio mechanics but ranks the same candidate population by the baseline percentile and has no ML `PROMOTE`/`REJECT` overlay.

The E1 momentum comparator is a same-mechanics comparator, not an exact-same-breadth comparator; persistent V4.1 abstention can therefore leave more cash.

## Replacement hysteresis

A full portfolio does not replace a holding merely because another candidate ranks slightly higher.

A replacement requires:

```text
challenger score >= weakest held score + 0.12
```

The **0.12** margin is predeclared before the E1 historical result is observed. It must not be changed retroactively to improve the E1 backtest. Testing another margin requires a new execution-policy version.

V4.1 uses its bounded final score. The baseline comparator uses baseline percentile, keeping both scores on an approximately 0–1 ranking scale.

## Historical sparse-observation rule

Historical V4.1 OOS research rows were sampled every five stock observations. A stock can therefore be absent on a historical research date simply because it was not sampled on that date.

To avoid manufacturing fake turnover:

- if a held stock has a fresh historical OOS observation and fails the candidate filter, it is treated as genuinely ineligible;
- if it has no fresh observation on that sampled date, E1 keeps the last valid candidate score and does not force an exit solely because of the sampling gap.

This approximation applies only to the historical E1 study. With complete future daily observations, ordinary candidate absence is a genuine ineligibility event.

## Historical comparison set

The E1 validator reports:

1. sticky E1 V4.1;
2. sticky E1 transparent momentum baseline;
3. the prior reactive V4.1 execution policy;
4. the prior reactive matched momentum baseline; and
5. buy-and-hold NEPSE over the same market-date window.

Daily paired-return differences are evaluated using the existing 20-session moving-block bootstrap.

## Predeclared research gate

E1 can become a candidate for a separate forward execution shadow only when all of these are true:

- at least 3 valid outer historical folds are available;
- E1 V4.1 CAGR is positive;
- E1 V4.1 CAGR is better than reactive V4.1;
- annualized turnover is **<= 30x**;
- annualized turnover falls by at least **60%** versus reactive V4.1;
- transaction cost is lower than reactive V4.1;
- mean completed holding period is at least **5 sessions**;
- E1 V4.1 CAGR is better than the E1 momentum baseline;
- the 20-session block-bootstrap lower 95% bound for E1 V4.1 minus E1 momentum is **> 0**; and
- E1 maximum drawdown is not worse than reactive V4.1.

Failure does not authorize changing these thresholds after seeing the result. A materially changed execution rule requires E2 (or another new version).

## Forward V4.1 shadow remains untouched

The predictive forward ledger continues to collect frozen V4.1 predictions independently of E1. E1 must not rewrite historical or future V4.1 prediction rows.

A new append-only `quant_v41_shadow_runs` heartbeat table records each shadow attempt as `captured`, `abstained`, or `failed`. Identical reruns de-duplicate by fingerprint; materially different retries remain as separate immutable audit events. This makes a successful no-setup day distinguishable from a missing pipeline run.
