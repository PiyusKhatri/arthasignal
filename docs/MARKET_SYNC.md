# Market Data Synchronization

ArthaSignal now separates two concepts that must never be conflated:

- **Trading day** — whether NEPSE is expected to trade on a calendar date.
- **Market open now** — whether the current wall-clock time is inside the intraday session.

The EOD pipeline uses the trading-day decision. Intraday capture additionally checks the 11:00–15:00 Nepal-time session window.

## Primary command

Run from the repository root with the Python virtual environment active:

```bash
python -m src.pipeline.sync_market_data
```

The default sync:

1. inspects database freshness,
2. repairs missing daily-price history,
3. repairs missing NEPSE/sub-index history,
4. refreshes settled EOD data when the session has been closed for at least 15 minutes,
5. rebuilds the trading calendar from confirmed market data,
6. recomputes technical signals,
7. recomputes liquidity tiers,
8. extracts and grades signal calls,
9. runs data-quality checks,
10. prints before/after freshness.

The operations are duplicate-safe through the existing PostgreSQL conflict handlers.

## Useful modes

Status only:

```bash
python -m src.pipeline.sync_market_data --status
```

Only the settled current session:

```bash
python -m src.pipeline.sync_market_data --today
```

Only historical gap repair:

```bash
python -m src.pipeline.sync_market_data --repair-gaps
```

Repair from a known date:

```bash
python -m src.pipeline.sync_market_data --repair-gaps --from 2026-08-01
```

Skip signal/liquidity recomputation:

```bash
python -m src.pipeline.sync_market_data --skip-signals
```

Machine-readable output:

```bash
python -m src.pipeline.sync_market_data --status --json
```

## Recovery behavior

Historical equity OHLCV is sourced from the existing Sharesansar history scraper and filtered to the required repair window before insertion. Index history uses the existing index-history scraper.

A confirmed `daily_prices` or `market_index` row is treated as stronger evidence than a stale `trading_calendar` value. Explicit named holidays are preserved. Legacy weekday rows that were stored as non-trading with no holiday name are treated as untrusted and repaired using the structural weekday pattern.

The old `GITHUB_ACTIONS=true` local workaround is no longer required.

## Scheduled daily pipeline

`src.pipeline.run_all_daily` performs an automatic historical gap repair when the database is materially stale, then continues the normal EOD, index, calendar, signals, validation, floorsheet, quality, backup, cleanup, and IPO-status workflow.

The automatic repair is intentionally skipped during normal Thursday-to-Sunday weekend spacing unless the calendar itself shows a missing expected trading session.

## Verification

```bash
python -m src.pipeline.sync_market_data --status
pytest -q
python -m compileall -q src tests
```

After a successful EOD sync, `latest_daily_price`, `latest_market_index`, and `latest_technical_signal` should normally point at the most recent confirmed trading session.
