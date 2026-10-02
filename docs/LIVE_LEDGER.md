# Live Call Ledger: Daily Writer

Status: **ready, dry-run verified, not scheduled.** Nothing has been written in `live` mode yet (`scorecard_calls` holds 0 live rows).

## What it does

`python -m src.scorecard.daily [--date YYYY-MM-DD] [--dry-run] [--schema public]`

1. Takes the signal session (default: the latest date in `daily_prices`). It exits **3** if that date has no price session.
2. Loads prices, corporate actions, the NEPSE Index and rates **up to that close only**, builds the panel with explicit corporate-action handling, and flags unresolved price steps (`docs/PRICE_INTEGRITY.md`).
3. Computes **model v0** (`src/scorecard/model_v0.py`) for that session:
   - abstains in a bear NEPSE state;
   - otherwise calls the top 10 by 20-session total return and the top 5 new listings;
   - skips stocks after a bonus book close (avoid rule E2) or after a ≥ 3-session upper-circuit streak ends (avoid rule E4);
   - states no probability.
4. Writes each call to the append-only ledger as `mode = 'live'`, with the model version, a feature hash, the situation labels and `created_at = now`. Inserts are `ON CONFLICT DO NOTHING` under a Postgres advisory lock, so rerunning the same day writes nothing new. The writer **refuses (exit 2) after 11:00 NPT on the day after the signal date**, and the database CHECK independently rejects any live row created after that time.
5. Grades every matured live call under `accuracy-v2` (all horizons whose exit session has a price), skipping grades that already exist.

## Model registry

`scorecard_models` (append-only, same triggers as the ledger) holds model v0:

| id | model | version | parameters hash | code commit | registered |
| ---: | --- | --- | --- | --- | --- |
| 1 | model_v0 | v0 | `46ba8c8c…75f0` | c3f800c | 2026-10-02 11:17 NPT |

Registering again returns the same row. If the v0 parameters in code change, the writer stops with "register a new version instead". The model is also registered as variant 70 in `backtest_variant_trials` (family `scorecard_accuracy_v2`).

## Dry run (verified)

`python -m src.scorecard.daily --dry-run --date 2025-01-16`, a session before the holdout, read no data after that date:
- market state `market_bull`;
- 10 calls (NYADI, BHPL, BEDC, UHEWA, RFPL, ULBSL, RHGCL, SGHC, SIKLES, USHEC), each with situation labels and a feature hash;
- "dry run: nothing written (a live write would be refused: deadline passed)";
- 0 live calls to grade.

`--date 2025-01-18` (not a session) exited 3.

Tests (`tests/test_scorecard_daily.py`):
- the deadline is 11:00 NPT the next day;
- v0 passes the look-ahead audit, abstains in a bear market and skips a recent bonus book close;
- live writes are idempotent (2 inserted, then 0) and refused after the deadline;
- the model registry rejects UPDATE.

## Running it on a server (not done here)

After the daily price pipeline has finished, and before 11:00 NPT the next morning. NEPSE closes at 15:00 NPT; check the current trading week before scheduling.

```
30 17 * * 0-4  cd /srv/arthasignal && .venv/bin/python -m src.scorecard.daily >> logs/scorecard_daily.log 2>&1
```

Exit codes: 0 done, 2 too late (deadline passed; nothing written), 3 no price session for the date. Run it only against the server's own Postgres; GitHub-hosted runners cannot reach a local database.

Live calls are the only evidence that counts as a production result (`docs/ACCURACY_PROTOCOL.md`). Running it on current dates necessarily reads data after 2025-09-30. That is the forward test this machine exists for, and it is separate from the frozen research holdout.
