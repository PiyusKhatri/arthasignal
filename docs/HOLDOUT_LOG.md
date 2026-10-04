# Holdout log

The research holdout starts on **2025-09-30** (`src/backtest/holdout_config.json`). Holdout labels may be read only through `final_evaluation()` (`src/backtest/holdout.py`), and every call is logged in `backtest_holdout_evaluations`. This file and the append-only table `holdout_incidents` record every other contact with holdout data.

## Final evaluations

None so far (`backtest_holdout_evaluations` is empty).

## Incidents

| # | Date | What happened | Rows returned | Used? |
| --- | --- | --- | --- | --- |
| 1 | 2026-10-04 | While investigating the WNLBP price step (Phase Q1), an ad hoc `psql` query (`SELECT symbol,date,open,high,low,close,volume FROM daily_prices WHERE symbol IN ('WNLBP') ORDER BY date`, last 30 rows printed) had no upper date bound | 2 rows: WNLBP 2025-10-08, open/high/low/close 1,138.00, volume 2,904; WNLBP 2025-10-13, 1,138.00, volume 1,185 | **No.** Seen in terminal output only; not stored, not used in any computation. The WNLBP quarantine rests on the 2025-07-14/15 rows and on the Sharesansar and Merolagani checks |

## Guard (added 2026-10-04)

The incident happened because research queries ran as a superuser with no date enforcement. Since 2026-10-04 there are two layers of protection:

1. **Database: row-level security.** Research code connects as the non-superuser role `arthasignal_research` (`src/database/holdout_guard.research_engine`). A `holdout_guard` policy on every date-keyed table hides rows dated on or after 2025-09-30 from that role, so a query with no date bound cannot return holdout rows. The tables are `daily_prices`, `market_index`, `corporate_actions`, `corporate_action_sources`, `technical_signals`, `trading_calendar`, `symbol_history`, `fundamentals`, `promoter_holding`, `intraday_floorsheet`, `intraday_snapshots`, `scorecard_calls`, `quarterly_report_announcements`, `quarterly_report_figures`, `dividend_declarations` and `quarterly_figure_captures`. The local login role is a superuser, and superusers bypass row-level security, which is why a separate role is needed. Ad hoc research queries should use `psql -U arthasignal_research`.
2. **Application: raise.** The research engine checks every statement before it runs. Any bound parameter (date, datetime, timestamp or ISO string), or any SQL date literal, on or after 2025-09-30 raises `HoldoutQueryViolation`. The one exception is a literal `'2025-09-30'` used as a strict `<` upper bound. The floorsheet Parquet listing (`broker_flow_features.floorsheet_files`) raises the same way.

All research modules in `src/backtest` and `src/scorecard` use the guarded engine. The guard can be opened only with a named reason, through `holdout_guard.allow(...)`:
- `final_evaluation`, opened inside `final_evaluation()`;
- `live_ledger`, opened by the live call writer when, and only when, it runs on the latest price session (the forward test). Any other holdout date is refused.

Production ingestion and the API use the unguarded owner connection, because they must write and serve current data. Tests: `tests/test_holdout_guard.py`.
