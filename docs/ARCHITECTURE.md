# ArthaSignal — System Architecture & Data Documentation

ArthaSignal is a **NEPSE (Nepal Stock Exchange) signal-validation and market-data platform**. It combines:

- a **Python FastAPI** backend that exposes a REST API,
- a **PostgreSQL** database that stores scraped and computed market data (now running locally in Docker, formerly Supabase cloud),
- a **Next.js / React / Tailwind** frontend that renders the dashboards and charts,
- a set of **ingestion + computation pipelines** that scrape external sources and derive technical signals / market pulse.

This document explains the overall architecture, how data is acquired and processed, the database schema, the API surface, and how to run the app locally.

---

## 1. High-Level Architecture

```

┌─────────────────────────────────────────────────────────────────┐
│                        BROWSER (client)                        │
│   React 19 / Next.js 16 / Tailwind 4                            │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP (same-origin, auth cookies)
┌───────────────────────────────▼─────────────────────────────────┐
│                FRONTEND  (port 3000)                             │
│   Next.js App Router                                          │
│   - Pages: / (landing), /login, /signup, /forgot-password,     │
│     /reset-password, /dashboard, /portfolio, /sectors,          │
│     /stock, /watchlist                                          │
│   - `app/api/*` route handlers act as a thin **proxy** to the   │
│     FastAPI backend (forwards requests, sets auth cookies)      │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP (API_BASE_URL, e.g. http://localhost:8000)
┌───────────────────────────────▼─────────────────────────────────┐
│                 BACKEND  (port 8000, FastAPI)                     │
│   Routers:                                                        │
│     /auth      – signup / login / password reset                  │
│     /stocks    – summary, technical, fundamental, signals,        │
│                 history, intraday, search                         │
│     /market    – pulse, sectors, active-signals, index history,   │
│                 index intraday                                     │
│     /portfolio – user holdings + summary                           │
│     /watchlist – user watchlist                                   │
│   Cross-cutting:                                                   │
│     - Rate limiting (slowapi)                                     │
│     - Response caching (cachetools TTLCache)                      │
│     - JWT authentication (PyJWT + bcrypt)                          │
│     - Read-only DB connection pool for public endpoints           │
└───────────────────────────────┬─────────────────────────────────┘
                                │ SQL (read + write pools)
┌───────────────────────────────▼─────────────────────────────────┐
│        DATABASE  (local Docker / PostgreSQL, port 5432)           │
│   Tables (see §4): companies, daily_prices, market_index,         │
│   technical_signals, signal_confidence, backtest_results,         │
│   intraday_snapshots, intraday_index_snapshots, intraday_         │
│   floorsheet, fundamentals, corporate_actions, watchlists,        │
│   holdings, users, ipo_calendar, brokers, macro_data …            │
└─────────────────────────────────────────────────────────────────┘
```

**Request flow for a page load:**

1. Browser requests `http://localhost:3000/dashboard`.
2. The Next.js page reads data via the client-side/landing data helpers (`frontend/src/lib/*`) or by calling a Next.js API route (`frontend/src/app/api/*/route.ts`).
3. The Next.js route handler proxies the request to the FastAPI backend at `API_BASE_URL` (e.g. `/stocks/SBL/summary` → `http://localhost:8000/stocks/SBL/summary`).
4. The backend applies rate-limiting + caching, then queries PostgreSQL (read-only pool) and returns JSON.
5. The frontend renders the JSON into charts and tables.

---

## 2. Where the Data Comes From

The API does **not** scrape on every request. The database is populated **offline** by ingestion scripts (`src/pipeline/`). The API simply reads the pre-populated tables.

### 2.1 Scraper / ingestion sources (`src/scrapers/`)

| Source | Module | Purpose |
|---|---|---|
| **NEPSE API** (via `nepse-scraper` package) | `nepse_api.py` | Primary source for today's prices (high/low/open/close), NEPSE index + sub-indices, intraday live data. |
| **Sharesansar** | `sharesansar_scraper.py` | Fallback for today's prices if NEPSE returns too few rows. |
| **Merolagani** | `merolagani_scraper.py` | Second fallback for today's prices. |
| **Floorsheet** | `floorsheet_scraper.py` | Broker-level trade contracts (used for broker concentration). |
| **Fundamentals** | `fundamentals_scraper.py` | EPS, PE, PB, book value, market cap, dividends. |
| **Index** | `index_scraper.py`, `intraday_index_scraper.py` | Historical + intraday NEPSE index snapshots. |
| **Intraday prices** | `intraday_price_scraper.py` | Per-symbol intraday LTP / volume so far. |
| **IPO calendar** | `ipo_calendar_scraper.py` | IPO issues, opening/closing dates, prices. |
| **Corporate actions** | `corporate_actions_scraper.py` | Bonus/right/dividend/split actions. |
| **Promoter holdings** | `promoter_holding_scraper.py` | Promoter/public share breakdown + lock status. |
| **Symbols / listing** | `symbols.py`, `symbol_history_scraper.py` | Listed companies + merger / name-change history. |
| **Macro data** | `macro_scraper.py` | Treasury bill, interbank rates, remittance, GDP. |

`get_today_price_with_fallback()` in `src/scrapers/market_data.py` implements the fallback chain: **NEPSE → Sharesansar → Merolagani**, each gated by a minimum expected row count (`MIN_EXPECTED_ROWS = 50`).

### 2.2 Pipeline / compute layer (`src/pipeline/`)

These scripts are run on a schedule (`run_daily.py`, `run_intraday.py`, `run_all_daily.py`) and are market-hours guarded (`market_hours_guard.py`). They do three kinds of work:

1. **Ingest / backfill** — `backfill_*.py` pull raw data from the scrapers into the DB (daily prices, index, intraday snapshots, floorsheets, fundamentals, IPO, corporate actions, macro, promoter holdings, symbol history).
2. **Derive indicators & signals** — `indicators.py` computes SMA/EMA/RSI/MACD/Stochastic/CCI/ROC/Bollinger/ATR/OBV/VWAP/pivots/Fibonacci + candlestick patterns (doji, hammer, engulfing, harami, morning/evening star, etc.); `compute_signals.py`, `compute_confluence_confidence.py`, `compute_signal_confidence.py`, `compute_baseline.py`, `compute_liquidity_tiers.py`, `compute_volume_conditional_tier.py`, `multi_timeframe_agreement.py`, `market_pulse.py` produce the confidence/confluence/pulse outputs.
3. **Backtest & validate signals** — `backtest_*.py` run historical backtests (momentum, volume confirmation, MTF agreement, liquidity-stratified, regime stability, confluence, market pulse) to produce the win-rate / edge tables used to label signals.

Key `run_*` entrypoints:

- `run_daily.py` — checks the trading calendar, fetches today's prices, upserts companies + daily prices, runs a data-quality health check, and re-applies corporate-action price adjustments.
- `run_intraday.py` — captures intraday price + index snapshots and index/price gap detection, then quality-checks coverage and (optionally) alerts to Discord.
- `run_all_daily.py` — orchestrates the full daily batch.
- `run_signal_backtests.py` — regenerates all backtest tables.

**Notifications:** `src/notifications/discord_alert.py` sends Discord alerts on pipeline failures / low intraday coverage.

---

## 3. API Surface (FastAPI) — `src/api/`

| Router | Endpoint | Description |
|---|---|---|
| **auth** (`auth.py`) | `/auth/signup`, `/auth/login`, `/auth/forgot-password`, `/auth/reset-password` | User auth (bcrypt + JWT). |
| **stocks** (`stocks.py`) | `/stocks/{symbol}/summary` | Latest price info. |
| | `/stocks/{symbol}/technical` | Full indicator set. |
| | `/stocks/{symbol}/signals` | Active signals + tiers + edge stats. |
| | `/stocks/{symbol}/fundamental` | Fundamentals + sector-relative P/E. |
| | `/stocks/{symbol}/history` | OHLCV price history. |
| | `/stocks/{symbol}/intraday-today` | Today's intraday points. |
| | `/stocks/search` | Symbol search. |
| **market** (`market.py`) | `/market/pulse` | Overall advance/decline, index, turnover. |
| | `/market/sectors` | Sector-wise pulse + broker concentration. |
| | `/market/sectors/{sector}/stocks` | Stocks in a sector with active signals. |
| | `/market/active-signals` | Active signals across the market. |
| | `/market/index-history?range=` | NEPSE index OHLC history (range e.g. 6M, 5Y). |
| | `/market/index-intraday-today` | Today's NEPSE index intraday line. |
| **portfolio** (`portfolio.py`) | `/portfolio/holdings`, `/portfolio/holdings/{id}`, `/portfolio/summary` | User portfolio CRUD + summary (JWT-protected). |
| **watchlist** (`watchlist.py`) | `/watchlist`, `/watchlist/{symbol}` | User watchlist (JWT-protected). |
| **health** (`main.py`) | `/health` | Service + DB connectivity check. |

**Caching** (`src/api/cache.py`) — in-memory TTL caches:

| Cache | TTL |
|---|---|
| `market_pulse_cache` | 60 s |
| `intraday_today_cache` | 60 s |
| `technical_signals_cache` | 900 s |
| `price_history_cache` | 900 s |
| `index_history_cache` | 900 s |

**Rate limiting** (`src/api/rate_limit.py`) — `slowapi` with public-route limits.

**Security** (`src/api/security.py`, `auth.py`) — bcrypt password hashing, JWT signing/verification using `JWT_SECRET_KEY`, auth dependency `get_current_user`.

---

## 4. Database Schema (PostgreSQL) — `src/database/models.py`

**Reference / market data**

| Table | Purpose |
|---|---|
| `companies` | Listed companies (symbol PK, name, sector, instrument type, status). |
| `daily_prices` | Daily OHLCV + turnover + adjusted close (symbol+date unique). |
| `market_index` | NEPSE index OHLC history (name+date unique). |
| `trading_calendar` | Trading days + holidays. |
| `symbol_history` | Merger / name-change history. |
| `corporate_actions` | Bonus/right/dividend/split actions. |
| `fundamentals` | EPS, PE, PB, book value, market cap. |
| `brokers` | Broker IDs + names. |
| `promoter_holding` | Promoter/public shares + lock status. |
| `ipo_calendar` | IPO issues and status. |
| `sector_index_mapping` | Company sector → market index name. |
| `sector_fundamental_baseline` | Sector-average PE / PB (outlier-excluded). |
| `short_term_interest_rates`, `remittance`, `gdp_nepse` | Macro series. |

**Intraday**

| Table | Purpose |
|---|---|
| `intraday_snapshots` | Per-symbol LTP, volume/turnover so far, day high/low. |
| `intraday_index_snapshots` | Index value snapshots during the session. |
| `intraday_floorsheet` | Trade contracts (buyer/seller broker, qty, rate, amount). |

**Signals / analytics**

| Table | Purpose |
|---|---|
| `technical_signals` | One row per (symbol, date, timeframe) with all indicators + candlestick pattern booleans. |
| `backtest_results` | Signal edge by (signal_name, forward_days). |
| `signal_confidence` | Tier, avg win-rate-minus-baseline, holding period, notes. |
| `signal_regime_stability` | Win-rate-minus-baseline by period. |
| `confluence_backtest_results`, `confluence_confidence` | Pairwise signal confluence stats + tiers. |
| `volume_confirmed_backtest_results`, `volume_conditional_tier` | Volume-conditioned edges. |
| `liquidity_stratified_backtest_results`, `symbol_liquidity_tier` | Liquidity tiers + stratified stats. |
| `mtf_agreement_backtest_results` | Multi-timeframe agreement edges. |
| `transaction_cost_adjusted_returns` | Net-of-cost return feasibility. |
| `market_pulse_backtest_results` | Pulse-condition forward returns + p-values. |
| `signal_calls` | Extracted/recorded signal calls with resolution + outcome. |

**Application / user data**

| Table | Purpose |
|---|---|
| `users` | Email + bcrypt-hashed password. |
| `holdings` | User portfolio positions. |
| `watchlists` | User watchlist entries. |
| `password_reset_tokens` | Reset tokens. |
| `system_notes` | Key/value operational notes. |

**Connection strategy** (`src/database/connection.py`, `db_readonly.py`):

- **Write pool** — `get_session()` (QueuePool, 90 s statement timeout, retry w/ exponential backoff) for writes (auth, portfolio, watchlist, pipeline).
- **Read-only pool** — `get_readonly_session()` used by public market/stock endpoints to spread read load and reduce risk. Uses `DATABASE_URL_READONLY`.

---

## 5. Frontend Structure — `frontend/src/`

| Path | Purpose |
|---|---|
| `app/page.tsx` | Landing page (hero, value props, market pulse widget, pricing). |
| `app/(app)/dashboard/page.tsx` | Main dashboard (market index chart, pulse). |
| `app/(app)/portfolio/`, `app/(app)/watchlist/` | JWT-protected routes (`proxy.ts` middleware redirects to `/login` if no token). |
| `app/(app)/sectors/`, `app/(app)/stock/` | Sector + per-stock pages. |
| `app/login`, `signup`, `forgot-password`, `reset-password` | Auth pages. |
| `app/api/*/route.ts` | Next.js route-handler **proxies** to the FastAPI backend. |
| `lib/api-config.ts` | `getApiBaseUrl()` — reads `API_BASE_URL` env var. |
| `lib/` | Data helpers (`market-data.ts`, `portfolio-data.ts`, `watchlist-data.ts`, `api-error.ts`, `auth-cookies.ts`, `validation.ts`, `signal-labels.ts`, `signal-tiers.ts`, `theme.ts`). |
| `components/` | `app-shell/` (sidebar/nav/drawer), `auth/`, `charts/` (lightweight-charts), `dashboard/`, `landing/`, `portfolio/`, `sectors/`, `stock/`, `watchlist/`. |

**Frontend → backend data path:**

```
frontend/src/app/(app)/dashboard/page.tsx
  → frontend/src/lib/market-data.ts
  → fetch(`${API_BASE_URL}/market/index-intraday-today`)
  → FastAPI backend  → PostgreSQL
```

---

## 6. Environment & Configuration

**Backend** (`src/config.py`, `.env` at repo root):

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Write DB connection (SQLAlchemy). Local: `postgresql://artha:artha_local_password@localhost:5432/artha`. |
| `DATABASE_URL_READONLY` | yes | Read-only DB connection. Local: same as `DATABASE_URL`. |
| `JWT_SECRET_KEY` | yes | JWT signing/verification key. |
| `DATABASE_PASSWORD` | no | DB password (used by some tooling). |
| `DISCORD_WEBHOOK_URL` | no | Pipeline failure / coverage alerts. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | no | Backup-to-Drive service account. |
| `GOOGLE_DRIVE_FOLDER_ID` | no | Backup folder target. |

**Frontend** (`frontend/.env.local`):

| Variable | Purpose |
|---|---|
| `API_BASE_URL` | Backend base URL, e.g. `http://localhost:8000`. |

---

## 7. How to Run

### 7.1 Backend (FastAPI)

> Note: The default system Python (3.14) triggers a SQLAlchemy 2.0.35 incompatibility with the `Mapped[...]` type annotations used by the models. Use **Python 3.11 / 3.12 / 3.13**.

```bash
# from the repo root
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# start the API on port 8000
.venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Verify: `curl http://localhost:8000/health` → `{"status":"ok","database":"ok"}`

Interactive API docs: `http://localhost:8000/docs`

### 7.2 Frontend (Next.js)

```bash
cd frontend
npm install
# create frontend/.env.local with:
# API_BASE_URL=http://localhost:8000
npm run dev
```

The app is served at `http://localhost:3000`.

### 7.3 Run the data pipelines (optional, ingest/compute)

```bash
# Daily ingest (prices, company upserts, adjustments)
.venv/bin/python -m src.pipeline.run_daily

# Intraday snapshots (market-hours guarded)
.venv/bin/python -m src.pipeline.run_intraday

# Recompute indicators + signals
.venv/bin/python -m src.pipeline.compute_signals
.venv/bin/python -m src.pipeline.compute_signal_confidence
.venv/bin/python -m src.pipeline.compute_confluence_confidence

# Re-run backtests
.venv/bin/python -m src.pipeline.run_signal_backtests
```

---

## 8. Data Flow Summary

```
External sources (NEPSE, Sharesansar, Merolagani, macro, IPO...)
   │  scrapers (src/scrapers)
   ▼
Raw data
   │  pipeline ingest/backfill (src/pipeline/backfill_*.py)
   ▼
PostgreSQL (local Docker) — companies, daily_prices, index, fundamentals, ...
   │  compute (indicators.py → compute_signals.py → confidence → backtest)
   ▼
Derived tables — technical_signals, signal_confidence, backtest_results, pulse
   │  FastAPI reads (read-only pool, cached, rate-limited)
   ▼
JSON API  (port 8000)
   │  Next.js proxy route handlers
   ▼
Browser UI  (port 3000)
```

---

## 9. Status

As of the last run, both services are live and healthy:

- Backend `http://localhost:8000` — health `{"status":"ok","database":"ok"}`, `/docs` available.
- Frontend `http://localhost:3000` — serving landing + dashboard pages (HTTP 200).