# ArthaSignal — Current Backlog

Last updated: 2026-08-20

This file tracks **current** blockers and follow-ups. Historical planning detail has been consolidated into the implemented code and versioned design documents so stale instructions do not contradict the running system.

## BEFORE COMMERCIAL LAUNCH — data permissions checklist

**Status:** OPEN. This does not block the personal research build (`docs/DATA_LICENSES.md`).

- [ ] NEPSE: obtain an official market-data licence (prices, floorsheet, notices) instead of the website token handshake used by `nepse_scraper`.
- [ ] Sharesansar: written permission for the announcements, AGM and dividend tables, news archive and report images collected under `src/archive/`.
- [ ] Bizmandu, Arthasarokar, Kathmandu Post and any other news portal: written permission for collection and for any redistribution.
- [ ] MeroLagani: confirm that the owner's written agreement covers the planned product and redistribution (scope recorded in `docs/DATA_LICENSES.md`).
- [ ] Remove or relicense any stored data whose permission is refused.

## BLOCKING FOR PUBLIC / PAID SIGNAL LAUNCH — confirm SEBON licensing status

**Status:** OPEN — external legal review required.

Consult an actual Nepal securities lawyer on whether publishing ArthaSignal trading signals — free or paid, automated or not — requires registration/licensing under SEBON investment-advisory, securities-business, portfolio-management, or related rules.

Do not treat a disclaimer, AI answer, or general web research as a substitute for that review.

Private paper-trade validation can continue while this is unresolved. Public signal publishing, marketing performance claims, and paid subscriptions remain blocked.

## PHASE 2 PREREQUISITE — confirm payment integration mechanics

**Status:** OPEN — not needed for private validation.

Before subscription engineering, confirm the real merchant flow for eSewa/Khalti (or another approved provider), especially:

- recurring vs manual renewal behavior;
- idempotency/retry handling;
- webhook verification;
- refund/cancellation behavior;
- settlement/reconciliation requirements.

Do not build payment code until the legal launch gate above is resolved and the actual provider contract is known.

## FORWARD VALIDATION STATISTICAL DESIGN

**Status:** DONE — protocol `2026-08-20-v1`.

The previous blocker requiring statistical design before relying on forward calls is resolved by:

- `src/pipeline/signal_validation_policy.py`
- `src/pipeline/validation_status.py`
- `docs/VALIDATION_PROTOCOL.md`

The v1 protocol:

- starts forward launch-gate evidence on 2026-08-20;
- scopes four historically high-confidence signals;
- uses 20-trading-day horizons;
- restricts doji to high-liquidity symbols;
- uses next-session open as entry;
- voids corporate-action-distorted calls;
- uses a 50 bps pre-committed round-trip cost assumption;
- clusters observations by entry trading day;
- requires minimum graded-call counts and at least 20 independent entry-date clusters;
- requires lower 95% confidence bounds above both 0% net return and 50% after-fee win rate;
- refuses to combine multiple signal-logic fingerprints in one v1 gate.

Changing those rules after observing outcomes requires a new protocol version and a new forward start date.

## VALIDATION STATUS CLI

**Status:** DONE.

Use:

```bash
python -m src.pipeline.validation_status
```

or:

```bash
python -m src.pipeline.validation_status --json
```

## AUTH / SESSION HARDENING

**Status:** DONE in repair branch; deployment initialization required.

Implemented:

- reset tokens are hashed at rest and never returned/logged;
- SMTP reset delivery;
- auth endpoint rate limits;
- one-time refresh-token rotation and server-side revocation;
- password reset revokes refresh sessions and previously issued access tokens;
- frontend protected routes/API calls refresh sessions automatically.

Deployment prerequisite after merge/pull:

```bash
python -m src.database.init_db
```

This creates the new `refresh_sessions` and `user_auth_state` tables without dropping existing tables.

## PRODUCTION DATABASE TOPOLOGY

**Status:** DECIDED 2026-10-04: one Ubuntu 24.04 server in AWS Mumbai runs Postgres (localhost only) and every scheduled job under systemd. Step-by-step runbook: `docs/PROD_DEPLOY.md`. The notes below describe the earlier options.

The local Docker PostgreSQL instance is valid for local development only. GitHub-hosted scheduled runners cannot reach a laptop-local database.

Choose one before relying on scheduled production ingestion:

1. use a properly secured network-reachable production PostgreSQL database and point the `DATABASE_URL` GitHub secret to it; or
2. use a self-hosted GitHub Actions runner beside the private database.

Scheduled workflows now fail fast if they are configured with `localhost`, `127.0.0.1`, or Docker hostname `db`.

## SMTP PASSWORD-RESET DELIVERY

**Status:** OPEN deployment configuration.

Configure the SMTP variables documented in `.env.example` before password reset is expected to work in production. When delivery is unavailable, the endpoint intentionally stays generic and leaves no newly usable reset token behind.

## SURPRISING-MISS DIAGNOSTICS

**Status:** DEFERRED.

After enough v1 calls resolve, consider logging unusually large negative net-return misses together with the signal-state snapshot at entry. Define the threshold before inspecting candidates to avoid outcome-driven tuning.

## VALIDATION MILESTONE NOTIFICATIONS

**Status:** DEFERRED.

Optionally reuse the existing Discord alert infrastructure for pre-defined milestones such as minimum call count reached, minimum independent entry days reached, or gate state change. Keep these notifications informational; they must not change the statistical protocol.

## READ-ONLY SIGNAL LEDGER API

**Status:** DEFERRED / B2B groundwork.

A private read-only API for the `SignalCall` ledger may be useful if a B2B licensing/API product is pursued. Do not expose it publicly before auth/access-control, legal, and data-contract requirements are defined.

## CI / REGRESSION COVERAGE

**Status:** BASELINE DONE; continue expanding.

`.github/workflows/ci.yml` now runs Python compile/tests and frontend lint/typecheck/build/smoke checks. New backend regression tests cover the hardening changes.

Future high-value additions:

- database-backed auth integration tests (signup/login/rotate/revoke/reset);
- API authorization tests across users;
- pipeline integration tests using fixture market data;
- browser tests for portfolio/watchlist refresh behavior;
- explicit stale-data UI tests;
- restore-test automation for database backups.
