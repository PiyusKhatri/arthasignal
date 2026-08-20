# E1 Forward Execution Shadow

**Forward protocol:** `2026-08-21-e1-forward-v1`  
**Execution policy:** `2026-08-21-execution-e1-v1`  
**Predictive artifact:** `artha-residual-alpha-v4.1-shadow-v1`  
**Status:** private forward paper-execution evidence only

## Purpose

Historical E1 research showed that patient execution materially reduced turnover and transaction-cost drag while preserving V4.1's relative edge. E1 still missed the historical 30x turnover and block-bootstrap gates, so it is not promoted.

The next step is not E2 tuning. This protocol freezes E1 and records genuinely future execution evidence tied to the already-frozen V4.1 prediction ledger.

## Immutable evidence chain

Every successful E1 forward date is linked to one successful V4.1 shadow heartbeat and the exact stored V4.1 candidate prediction fingerprints for that date.

The forward process stores:

- one append-only E1 run heartbeat for the signal date;
- every E1 candidate's baseline rank and percentile;
- frozen V4.1 final score and override action;
- expected excess return;
- market/sector/liquidity context;
- V4.1 and baseline entry eligibility; and
- a SHA-256 decision fingerprint.

The E1 capture recomputes the frozen artifact only as a consistency check. If the candidate symbol set, final score, override action, or expected excess return differs from the already-stored V4.1 shadow row, E1 records a failed attempt and does not create forward decisions.

A successful E1 signal date is never overwritten. Later portfolio reports replay the append-only decision history rather than mutating a saved paper-account state.

## Execution convention

A V4.1/E1 decision uses all information from completed market session **D**.

It therefore cannot execute at D's already-observed close.

Forward E1 execution is precommitted as:

```text
completed session D
→ freeze E1 candidate decisions
→ next tradable session D+1
→ execute buys/sells at that stock's recorded open
→ mark positions at D+1 close
```

If a stock has no recorded trade/open on the intended execution session, the simulator cannot invent an execution. Existing E1 blocked-trade behavior applies.

This next-session-open convention is stricter than the historical E1 execution diagnostic and is intentionally evaluated as new forward execution evidence rather than used to rewrite historical results.

## Frozen E1 rules

Forward replay keeps the existing E1 v1 rules unchanged:

- maximum positions: **5**;
- maximum holding period: **20 NEPSE sessions**;
- round-trip cost assumption: **1.00%**;
- no routine rebalancing of surviving holdings;
- a surviving holding is not sold merely because its rank drifts;
- a full portfolio replaces the weakest valid holding only when the challenger exceeds its score by **0.12**;
- V4.1 `REJECT`, genuine current candidate ineligibility, 20-session expiry, and qualifying replacement remain the exit mechanisms;
- no leverage; and
- no shorting.

The transparent momentum comparator receives the same E1 mechanics. It uses the exact baseline percentile captured on each future signal date.

## Successful abstention

An E1 date can contain zero candidates. This is still evidence.

If the corresponding V4.1 heartbeat is a successful `abstained` run, E1 records an immutable abstained signal date with zero decision rows. On the next tradable session, that empty candidate set is replayed as a genuine complete-daily observation rather than treated as a missing pipeline run.

This differs from the old historical five-observation matrix, where absence could mean the stock simply was not sampled.

## Forward acceptance gate

The gate is predeclared before the first non-empty E1 forward candidate date.

Evidence remains `collecting` until both are available:

- at least **120 successful E1 signal dates**; and
- at least **40 completed V4.1 E1 trades**.

After maturity, forward E1 passes only when every condition below is true:

- no successful V4.1 source date is missing its E1 capture;
- V4.1 E1 compounded total return is positive;
- V4.1 E1 compounded total return is better than the same-mechanics E1 momentum portfolio;
- annualized V4.1 E1 turnover is **<= 30x**;
- mean completed V4.1 E1 holding period is at least **5 sessions**;
- V4.1 E1 maximum drawdown is no worse than E1 momentum; and
- the lower bound of the 95% 20-session moving-block bootstrap for V4.1 E1 minus E1 momentum annualized incremental return is **> 0**.

A mature sample that does not satisfy every check becomes `review`, not `pass`.

## Promotion boundary

A forward E1 `pass` does not replace V1 and does not automatically promote V4.1. It only earns manual champion review alongside the independent V4.1 prediction-level forward gate.

The predictive V4.1 ledger and E1 execution ledger therefore answer two different questions:

1. **Does frozen V4.1 select better stocks than the transparent baseline?**
2. **Can frozen E1 turn those predictions into a realistic low-churn portfolio after execution costs?**

Both evidence streams remain versioned and independent.
