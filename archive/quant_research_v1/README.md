# Archived quant research, round 1 (V2 to V5, E1)

Parked on 2026-09-30. Nothing here is imported by the application, the data pipelines or the scheduled workflows. Nothing was deleted: every file was moved with `git mv`, so history is intact.

Why it is parked: no version passed its own pre-declared gate, none beat buy-and-hold NEPSE, and there was no untouched holdout. The full evidence is in `docs/HONEST_STATUS.md`. Paths cited there as `src/pipeline/...`, `src/services/...`, `tests/...` or a root-level result file now live under this directory.

## Layout

| Directory | Contents |
| --- | --- |
| `pipeline/` | Validators and shadow-capture commands, formerly `src/pipeline/` |
| `services/` | Model, policy and simulator code, formerly `src/services/` |
| `tests/` | Their unit tests, formerly `tests/` |
| `results/` | Result dumps, formerly in the repository root |

Imports were rewritten from `src.pipeline.X` / `src.services.X` to `archive.quant_research_v1.pipeline.X` / `.services.X`. Code that stayed in `src/` (V1 features, XGBoost helpers, pooled-row builder) is still imported from `src`.

Run the archived tests with:

```bash
python -m pytest -q archive/quant_research_v1/tests
```

170 tests passed at the time of the move.

## What each version was and its recorded result

All results assume a 1.00% round-trip cost. "Gate" is the version's own all-checks-must-pass research gate.

| Version | What it was | Recorded result | Gate |
| --- | --- | --- | --- |
| V2 | Execution-aware XGBoost ranker plus classifiers | Top-10 mean net excess +0.44%, precision 42.3% | Not passed, 2 of 8 checks failed |
| V3 | Regime-aware, risk-gated decision policy | 33 calls; mean net excess +0.015%, median −2.16%; baseline +6.97% | Not passed, 6 of 10 failed |
| V4 | Residual-alpha model over a momentum baseline | 942 calls; mean net excess +3.12%, median +0.07%; beat baseline in 2 of 4 folds | Not passed, 3 of 11 failed |
| V4.1 | Stability policy on the V4 architecture | 712 calls; mean net excess +2.75%, median −0.31%; portfolio simulation −67.8% vs NEPSE +81.5% | Not passed, 4 of 11 failed |
| E1 | Low-churn execution policy for V4.1 predictions | CAGR +9.79% vs NEPSE +12.06%; turnover 34.25x | Not passed, 2 of 10 failed |
| V5 | Point-in-time stability challenger, three model heads | Never evaluated on record | Not run |

V2 was not named in the original triage request. It is archived with the rest because it failed its gate in the same way and the later versions depend on its helper module.

## File map

### `pipeline/`

| File | Version | Purpose |
| --- | --- | --- |
| `validate_execution_challenger.py` | V2 | Nested walk-forward validator |
| `validate_regime_decision_v3.py` | V3 | Validator |
| `validate_residual_alpha_v4.py` | V4 | Validator |
| `validate_residual_stability_v41.py` | V4.1 | Validator |
| `validate_v41_portfolio.py` | V4.1 | Daily mark-to-market portfolio simulation |
| `v41_shadow.py` | V4.1 | Forward shadow capture and grading |
| `validate_execution_policy_e1.py` | E1 | Historical execution validator |
| `diagnose_execution_policy_e1.py` | E1 | Turnover and trade diagnostics |
| `e1_forward_shadow.py` | E1 | Forward execution capture |
| `validate_quant_v5.py` | V5 | Validator (no recorded run) |

### `services/`

| File | Version |
| --- | --- |
| `quant_execution_aware.py` | V2 (shared by later versions) |
| `quant_decision_policy.py`, `quant_investability.py` | V3 |
| `quant_residual_alpha.py`, `quant_historical_context.py`, `quant_predictable_investability.py` | V4 |
| `quant_residual_stability.py`, `quant_v41_artifact.py`, `quant_v41_heartbeat.py`, `quant_v41_live.py`, `quant_v41_validation.py`, `quant_portfolio_simulator.py` | V4.1 |
| `quant_execution_policy_e1.py`, `quant_execution_diagnostics.py`, `quant_e1_forward.py` | E1 |
| `quant_v5_dataset.py`, `quant_v5_model.py`, `quant_v5_training.py`, `quant_v5_validation.py` | V5 |

### `results/`

| File | Version | Content |
| --- | --- | --- |
| `extracted_metrics.json` | V2 | Extracted validator metrics |
| `v3_extracted.json` | V3 | Extracted validator metrics |
| `v4_extracted.json`, `v4_extra_extracted.json` | V4 | Summary and per-fold detail |
| `v41_extracted.json` | V4.1 | Summary and per-fold detail |
| `v41_portfolio_extracted.json` | V4.1 | Portfolio simulation |
| `v41_shadow_extracted.json` | V4.1 | Forward snapshot of 2026-08-20: 0 candidates, 0 ledger rows |
| `e1_extracted.json`, `e1_diag_extracted.json`, `e1_diagnostics.txt` | E1 | Summary, diagnostics, full diagnostics output |

## What was left in place

- Database models for the V4.1 and E1 ledgers (`src/database/quant_models.py`, `src/database/e1_models.py`). The tables exist in the database and `init_db` still registers them.
- Protocol documents in `docs/` (`V41_FORWARD_VALIDATION.md`, `V5_CHALLENGER_PROTOCOL.md`, `E1_EXECUTION_POLICY.md`, `E1_FORWARD_EXECUTION.md`). Commands in them now need the `archive.quant_research_v1.pipeline.` module prefix.

## Consequence

The daily workflow no longer runs the V4.1 shadow or the E1 forward capture, so those forward ledgers stop collecting. To resume, run `python -m archive.quant_research_v1.pipeline.v41_shadow` and `python -m archive.quant_research_v1.pipeline.e1_forward_shadow`.
