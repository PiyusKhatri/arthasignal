# Keep, Park, Remove

Triage of the 224 commits merged on 2026-09-30. Evidence for the quant verdicts is in `docs/HONEST_STATUS.md`. Nothing was deleted; "park" means moved to `archive/quant_research_v1/` with `git mv`.

After the move, `pytest -q` passes 302 tests: 132 in `tests/` and 170 in `archive/quant_research_v1/tests/`.

## Keep (untouched)

| Area | Where | Reason |
| --- | --- | --- |
| Auth and session hardening | `src/api/auth.py`, `security.py`, `dependencies.py`, `email_service.py`, `src/database/auth_models.py` | Working product code; the database-backed integration test passes locally |
| CI | `.github/workflows/ci.yml` | Runs compile, tests and frontend checks |
| Market sync and gap repair | `src/pipeline/sync_market_data.py`, `tests/test_market_sync.py` | Data integrity; used by the daily pipeline |
| All data pipelines | `src/pipeline/run_*.py`, `backfill_*.py`, `compute_*.py`, scrapers | The data is the asset every future model needs |
| Signal-call validation | `signal_validation_policy.py`, `validation_status.py`, `extract_signal_calls.py`, `grade_signal_calls.py` | The one forward protocol with a sound design: next-open entry, date clustering, fixed thresholds |
| Intelligence API and UI | `src/api/intelligence.py`, `src/services/stock_intelligence.py`, `ai_analyst.py`, `frontend/src/components/intelligence/` | User-facing feature |
| Alerts, watchlist, portfolio | `src/api/alerts.py`, `watchlist.py`, `portfolio.py`, `src/pipeline/check_alerts.py` | User-facing features |
| V1 quant stack | `quant_features.py`, `quant_xgboost.py`, `quant_model_store.py`, `quant_cross_sectional.py`, `nepse_quant_research.py`, `quant_robustness.py`, `train_quant_models.py`, `quant_shadow.py`, `quant_validation.py`, `validate_quant_walk_forward.py`, `validate_quant_robustness.py` | The intelligence API imports it, and the daily workflow trains and shadows it. Kept because it is wired in, not because it is proven: no V1 result is recorded |
| Quant ledger tables and models | `src/database/quant_models.py`, `e1_models.py`, `tests/test_quant_contract.py` | Tables exist in the database; dropping models would orphan them |
| Protocol documents | `docs/V41_FORWARD_VALIDATION.md`, `V5_CHALLENGER_PROTOCOL.md`, `E1_*.md`, `VALIDATION_PROTOCOL.md` | Record of what was claimed and pre-declared |

## Park (moved to `archive/quant_research_v1/`)

| Item | Files | Reason |
| --- | --- | --- |
| V2 execution-aware challenger | 1 pipeline, 1 service, 1 test | Failed 2 of 8 gate checks. Not named in the request; parked because it failed like the others and later versions import its helper |
| V3 regime decision policy | 1 pipeline, 2 services, 3 tests | Failed 6 of 10 checks; 33 calls; baseline did better |
| V4 residual alpha | 1 pipeline, 3 services, 3 tests | Failed 3 of 11 checks; beat baseline in 2 of 4 folds |
| V4.1 residual stability | 3 pipelines, 6 services, 2 tests | Failed 4 of 11 checks; portfolio simulation lost 67.8% |
| E1 execution policy | 3 pipelines, 3 services, 3 tests | Failed 2 of 10 checks; trailed NEPSE |
| V5 stability challenger | 1 pipeline, 4 services, 6 tests | Never evaluated; designed on the same already-inspected history |
| Result dumps | 9 JSON files and `e1_diagnostics.txt` from the repository root | Research output, not source; cluttered the root |

Common reasons for parking the whole line of work:

- No untouched holdout: each validator reports `fresh_untouched_historical_holdout: False`.
- Five successive designs were tuned against the same 2020 to 2026 test window.
- Historical entry was at the signal-day close, which cannot be traded.
- Nothing beat buy-and-hold NEPSE.

The replacement foundation is described in `docs/BACKTEST_PLAN.md`.

## Remove

Nothing was removed.

- The file named `git log -n 5` appears in git history but is not in the current tree, tracked or untracked, so there was nothing to delete.
- No other accident files were found (no names with spaces, backup files, merge leftovers or empty files).

## Necessary side changes

| Change | Reason |
| --- | --- |
| Two steps dropped from `.github/workflows/daily_pipeline.yml` (V4.1 shadow, E1 forward capture) | Their modules moved; the scheduled job would otherwise fail |
| Imports rewritten inside the archive | So the 170 archived tests still pass |
| `requirements.txt`: `psycopg2-binary` 2.9.9 to 2.9.13 | 2.9.9 does not build on Python 3.13 |

## Left for a decision

| Item | Note |
| --- | --- |
| V1 weekly retraining in the daily workflow | The "champion" is retrained weekly with no recorded validation. Consider freezing it or labelling its output experimental |
| `venv/` | A virtual environment created on another machine; ignored by git and unusable here. A working one is in `.venv/` |
| `.agents/`, editor skill folders, `skills-lock.json` | Tooling files, not application code; left alone |
| `docs/ARCHITECTURE.md` | Out of date: omits quant, intelligence and alerts |
