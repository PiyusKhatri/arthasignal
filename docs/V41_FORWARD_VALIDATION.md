# V4.1 Frozen Challenger Validation

**Model version:** `artha-residual-alpha-v4.1-shadow-v1`  
**Policy version:** `2026-08-21-residual-stability-v1`  
**Forward gate:** `2026-08-21-v41-forward-v1`  
**Status:** research challenger only; V1 remains the live champion

## Freeze boundary

The V4.1 architecture, features, candidate-first baseline, override rules, risk rules, cost hurdle, and acceptance thresholds are frozen before forward evidence is collected.

The deployment artifact is fitted once from matured historical observations using a purged chronological core-training/calibration split. It is then stored in `quant_v41_model_snapshots` with a SHA-256 artifact fingerprint. Re-running the freeze command for the same model version returns the existing artifact instead of retraining it. Any future retraining or policy change requires a new model version and a new forward ledger.

## Immutable prediction ledger

Every future candidate is written to `quant_v41_shadow_signals`. Capture fields are insert-only and include:

- symbol and as-of date;
- frozen model snapshot and artifact fingerprint;
- baseline rank and V4.1 rank;
- V4.1 and exact-same-breadth baseline selection flags;
- `promote` / `hold` / `reject` action;
- predicted residual alpha and probability of positive residual;
- predicted MAE, MFE, and payoff ratio;
- market regime, sector regime, and point-in-time liquidity bucket;
- entry stock and NEPSE prices; and
- a per-row SHA-256 prediction fingerprint.

Only resolution fields may be filled later. Historical predictions are never recomputed in place.

## Point-in-time live universe

A forward candidate must:

1. be an active NEPSE equity;
2. have an actual stock trade on the latest completed NEPSE session;
3. have enough price history for the frozen feature contract; and
4. be in the medium/high same-date terciles of trailing 20-observation average turnover.

Live market and sector regime context is recreated from index observations available on or before that session. Future horizon slippage or future trading availability is never used to decide current eligibility.

## Resolution

The target is 20 NEPSE trading sessions after capture. If the stock does not trade on the target session, resolution may move forward by at most three NEPSE sessions. A call is void when no stock trade exists by that grace-window cutoff or when a corporate action occurs during the raw-price grading window.

Resolved rows store stock return, NEPSE return, excess return, realized MAE, realized MFE, and success after the fixed 1.00% execution hurdle.

## Forward comparison

V4.1 and the transparent momentum baseline are compared only on dates where both sides have fully resolved at exactly the same breadth. The forward gate reports:

- mean and median incremental alpha;
- positive incremental-date rate;
- date-bootstrap 95% interval;
- non-overlapping 20-session cohort bootstrap;
- severe-drawdown rate and mean MAE for both strategies; and
- relative severe-drawdown reduction.

The gate remains `collecting` until it has at least 80 matched resolved V4.1 calls, 20 matched dates, and 12 non-overlapping cohorts. A statistical `pass` never promotes the model automatically; it only makes V4.1 eligible for manual champion review.

## Historical portfolio simulator

`src.pipeline.validate_v41_portfolio` uses the already frozen four-fold OOS V4.1 selections and exact matched baseline selections. It simulates:

- maximum 5 positions;
- equal weights;
- 1.00% round-trip transaction cost;
- maximum 20 NEPSE-session holding period;
- close-of-session rebalancing on every available OOS decision date;
- no leverage and no shorting;
- daily mark-to-market;
- blocked trading when a stock has no exact print on the execution date;
- turnover, transaction costs, holding duration, CAGR, volatility, Sharpe-like ratio, and drawdown; and
- a 20-session moving-block bootstrap of V4.1 minus baseline daily returns.

Historical V4.1 research rows were sampled every five stock observations. Therefore this simulator is daily mark-to-market with periodic genuine OOS decision rebalancing; it does not claim a historical V4.1 signal existed on every NEPSE session. The forward ledger removes that limitation by capturing each future eligible session.

## Commands

Initialize the new tables:

```bash
python -m src.database.init_db
```

Run focused tests:

```bash
python -m pytest -q \
  tests/test_quant_v41_forward.py \
  tests/test_quant_contract.py \
  tests/test_quant_residual_stability.py \
  tests/test_quant_residual_alpha.py \
  tests/test_quant_v4_leakage.py
```

Run the historical execution simulator:

```bash
python -m src.pipeline.validate_v41_portfolio --limit 400 --folds 4
```

Freeze the deployment artifact once and capture the first forward session:

```bash
python -m src.pipeline.v41_shadow --limit 300 --train-if-missing
```

Subsequent daily runs must omit `--train-if-missing`:

```bash
python -m src.pipeline.v41_shadow --limit 300
```

The branch daily workflow runs the latter command after the existing quant shadow step. Scheduled GitHub workflows execute from the default branch, so the workflow change does not become production-active while this work remains only on the repair branch.

## Change control

Do not change V4.1 thresholds, features, model parameters, liquidity eligibility, horizon, transaction-cost hurdle, or forward gate thresholds after forward outcomes are observed. Any such change requires a new model version, new artifact fingerprint, and new forward evidence ledger.
