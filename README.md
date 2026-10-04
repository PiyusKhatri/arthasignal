# ArthaSignal

ArthaSignal is a NEPSE market-intelligence and forward signal-validation platform. It combines scheduled market-data ingestion, technical/fundamental analytics, an evidence-oriented paper-trade ledger, a FastAPI API, and a Next.js dashboard.

The product should be described as **market intelligence with evidence-backed signals**, not as guaranteed prediction or guaranteed trading returns. The public/paid signal phase remains gated by forward validation and Nepal securities-law review.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy
- **Database:** PostgreSQL 17
- **Frontend:** Next.js 16, React 19, Tailwind CSS 4
- **Automation:** GitHub Actions for scheduled pipelines and CI
- **Auth:** bcrypt passwords, short-lived JWT access tokens, rotating/revocable refresh sessions

See `docs/ARCHITECTURE.md` for the broader data model and pipeline map.

## Local development

### 1. Start PostgreSQL

```bash
docker compose up -d
```

The included compose file starts PostgreSQL 17 at `127.0.0.1:5432` with the local-development credentials already reflected in `.env.example`.

### 2. Configure the backend

```bash
cp .env.example .env
```

For local development, the default `DATABASE_URL` in `.env.example` is suitable. Replace `JWT_SECRET_KEY` with a long random value before testing authenticated flows.

`DATABASE_URL_READONLY` is optional. If it is absent or blank, the application reuses `DATABASE_URL`.

### 3. Install backend dependencies and initialize tables

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.database.init_db
```

Run `python -m src.database.init_db` after pulling this hardening update so the `refresh_sessions` and `user_auth_state` tables are created. It uses SQLAlchemy `create_all`, so existing tables are not dropped.

### 4. Start FastAPI

```bash
uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl -i http://127.0.0.1:8000/health
```

A healthy instance returns HTTP 200. Database connectivity failure returns HTTP 503 rather than a false healthy response.

### 5. Configure and start the frontend

```bash
cd frontend
cp .env.local.example .env.local
npm ci
npm run dev
```

The app is then available at `http://localhost:3000`.

## Authentication and password reset

Access tokens expire after 15 minutes. Refresh tokens last up to 7 days, are stored only as SHA-256 digests server-side, and are rotated after every successful refresh. A password reset revokes all refresh sessions and invalidates previously issued access tokens.

Password-reset bearer tokens are never returned by the API and are never logged. Only a SHA-256 digest is stored in PostgreSQL. To enable delivery, configure:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME` / `SMTP_PASSWORD` when required by the provider
- `SMTP_FROM_EMAIL`
- `SMTP_USE_TLS`
- `FRONTEND_BASE_URL`

If SMTP is missing or delivery fails, the forgot-password endpoint intentionally returns the same generic response and does not persist a usable new reset token.

Legacy plaintext reset tokens created by older code are intentionally no longer accepted.

## Scheduled pipelines: important deployment rule

The production setup for the prediction system (one server, Postgres on localhost, systemd timers, Discord reports, encrypted backups) is in `docs/PROD_DEPLOY.md`. The GitHub workflow notes below apply only if you run the legacy workflows.

The local Docker database is for local development. A GitHub-hosted Actions runner cannot connect to a database that exists only on a developer laptop or at `localhost`.

For the scheduled daily, intraday, and fundamentals workflows, choose one production topology:

1. point the `DATABASE_URL` GitHub secret at a network-reachable PostgreSQL instance with appropriate firewall/TLS controls, or
2. run those workflows on a self-hosted runner located beside the private database.

The workflows now perform a database connectivity preflight and explicitly fail when configured with `localhost`, `127.0.0.1`, or the Docker-only hostname `db`. This avoids silent split-brain operation between local and scheduled data.

## Forward paper-trade validation

The launch-gate protocol is versioned in `src/pipeline/signal_validation_policy.py` and documented in `docs/VALIDATION_PROTOCOL.md`.

Current v1 protocol starts on **2026-08-20**. Calls before that date can remain diagnostic history but are excluded from launch-gate evidence because the statistical rule was not pre-committed at the time.

Check progress with:

```bash
python -m src.pipeline.validation_status
```

Machine-readable output:

```bash
python -m src.pipeline.validation_status --json
```

Changing a signal definition, fee assumption, horizon, minimum sample, clustering rule, or pass/fail threshold after looking at outcomes requires a new protocol version and a new forward start date.

## Verification

Backend:

```bash
pytest -q
python -m compileall -q src
```

Frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm run build
```

The repository also has a Playwright-backed authentication smoke test:

```bash
npx playwright install chromium
npm run start -- -H 127.0.0.1 -p 3000
# in another shell
npm run test:smoke
```

`.github/workflows/ci.yml` runs the backend and frontend verification automatically on pushes and pull requests.

## Production launch gates

Engineering readiness is not the only launch gate. Before publishing or charging for trading signals, the existing project plan requires an actual Nepal securities lawyer to confirm how SEBON investment-advisory rules apply to this product. Do not replace that review with a disclaimer or AI/web research.

Private paper-trade validation can continue while that legal review is pending.

## Security notes

- Never commit `.env`, SMTP credentials, JWT secrets, database credentials, Discord webhooks, or Google service-account credentials.
- Use a distinct high-entropy `JWT_SECRET_KEY` in every deployed environment.
- Put production PostgreSQL behind network controls; do not expose the local Docker password publicly.
- Run database backups and verify restore procedures before relying on the app for operational data.
- Treat all signal output as probabilistic research output, not guaranteed financial performance.
