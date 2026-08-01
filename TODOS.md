# TODOS

## [BLOCKING FOR PHASE 2] Confirm SEBON licensing status before any public/paid signal launch

**What:** Consult an actual Nepal securities lawyer (not AI research) on whether publishing trading signals — free or paid, automated or not — requires registration/licensing under SEBON's Securities Businessperson (Investment Advisor) Directive, or any other Nepal capital-markets regulation.

**Why:** Web research (2026-08-01, `/plan-ceo-review`) confirms SEBON regulates investment-advisory and portfolio-management activity in Nepal (CFA certification, capital requirements, professional indemnity insurance for licensed advisors) — but general search cannot determine whether an automated signal/track-record publishing product specifically falls under that regime. An outside-voice cross-model review flagged this as the single most important gap in the entire plan: Phase 2 (public signals, paid subscription) could be illegal as scoped if it counts as unlicensed investment advisory activity.

**Pros:** Resolves the single biggest existential risk to the business before more engineering/business time is invested in Phase 2; may reveal Phase 2 needs restructuring (e.g., "informational only, not advice" framing, or an actual license) rather than a straightforward launch.

**Cons:** Real cost (a lawyer's time/fee) and calendar time before Phase 2 can proceed with confidence.

**Context:** Phase 1 (private paper-trade validation — no public claims, no payment, no publishing) does NOT carry this same immediate exposure and can proceed now. This blocks Phase 2 (public Telegram/Viber channel + paid subscription) specifically, not the current validation-engine work.

**Depends on / blocked by:** Nothing blocks starting this — can run in parallel with Phase 1 engineering. Blocks: any Phase 2 public launch, marketing, or payment integration work.

## [Phase 2 prerequisite] Confirm payment integration approach (eSewa/Khalti) including recurring billing limits

**What:** Register for eSewa and/or Khalti merchant APIs (or evaluate an aggregator like PayBridgeNP), and specifically confirm whether true auto-recurring billing is supported or whether subscriptions require a manual "renew monthly" flow.

**Why:** The chosen business model (Model A, direct-to-consumer subscription) assumes Nepal-based traders can pay recurring subscriptions, but this was unverified — flagged by an outside-voice cross-model review during `/plan-ceo-review` (2026-08-01) as core-model-load-bearing, not a side detail. Web research confirms both providers have solid merchant APIs, but recurring/subscription-specific billing features aren't clearly documented — likely one-time-charge APIs requiring a manual renewal flow rather than auto-charge.

**Pros:** Confirms the actual payment mechanics before building any billing code; if auto-recurring isn't supported, the product needs a manual-renewal reminder flow instead, which is a real (if modest) design decision worth knowing now.

**Cons:** Requires merchant registration and possibly a paid aggregator service (PayBridgeNP or similar) depending on which path is chosen.

**Context:** Also watch for idempotency-key handling — a Khalti integration without it creates double-charge risk on network timeouts/resubmissions (per `neptechpal.com.np` and `praxiumlabs.com` guides).

**Depends on / blocked by:** Not blocked by anything, but has no urgency until Phase 2 (payment doesn't exist in Phase 1's private validation).

## [BLOCKING] Finish statistical design (sample size + cross-sectional independence) before extraction script implementation starts

**What:** Two things, both required before `extract_signal_calls.py`/`grade_signal_calls.py` implementation begins (not just before results are interpreted):
1. Query existing `BacktestResult` (and/or `TechnicalSignal`) data to determine how often each of the 25 signals/patterns actually fired historically, bucket signals into frequency tiers, and propose a minimum sample-size threshold per tier.
2. Design the significance test to account for cross-sectional non-independence: calls firing across many tickers on the same trading day are correlated with a single NEPSE-wide market move (a broad rally/selloff moves most signals' hit rate together regardless of quality). A 4-6 week window on a thin market is exactly the regime where one or two macro days could dominate the result. The test needs to cluster/adjust by trading day, not treat every call as an independent draw — and the schema (`SignalCall`) needs to retain whatever date/grouping granularity that clustering requires.

**Why:** Reusing `compute_signal_confidence.py`'s `LOW_SAMPLE_THRESHOLD=500` unmodified was rejected during `/plan-eng-review` (2026-08-01) as miscalibrated for a short window. A follow-up outside-voice review during `/plan-ceo-review` (2026-08-01) found a second, more fundamental gap: even a well-chosen sample size doesn't fix non-independent samples. The founder decided to pause coding until BOTH are resolved, rather than build the extraction schema first and risk it not capturing what the eventual test needs (e.g., trading-day grouping).

**Pros:** The validation gate's result will actually mean what it claims to mean — a real signal-quality signal, not an artifact of one volatile week or an underpowered rare-signal bucket. Prevents rework if the schema built first turns out to lack the granularity the stats design needs.

**Cons:** Delays starting `extract_signal_calls.py`/`grade_signal_calls.py` implementation until this design work is done.

**Context:** This came out of two separate cross-model tensions: `/plan-eng-review`'s outside voice (sample-size calibration) and `/plan-ceo-review`'s outside voice (cross-sectional independence + sequencing). The founder explicitly chose "pause coding until statistical design is finished" over "code now, patch stats later."

**Depends on / blocked by:** Blocks the extraction script implementation (`extract_signal_calls.py`), the grading job (`grade_signal_calls.py`), and the exact pass/fail bar definition (design doc Open Questions). Not blocked by anything — both parts are read-only analysis/design work against existing data, can start immediately.

## [DONE] Check what existing backtests already show before committing to live validation

**What:** Before starting the statistical design work above, pull the current `BacktestResult`/`SignalConfidence` numbers for the 25 signals and see what they already show.

**Why:** An outside-voice review during `/plan-ceo-review` (2026-08-01) pointed out this is free, already-available information that neither the design doc nor CEO plan referenced. If existing backtests already show hit rates near or below coin-flip+fees for several signals, retune those signals first using data that already exists (instant) rather than waiting 4-6 weeks of live validation to learn the same thing. If backtests already show strong significance, that changes the risk calculus of the whole gate (higher prior confidence the live validation will pass).

**Pros:** Near-zero cost (a read-only query against data that already exists), could save weeks of live validation time on signals that are backtested weak.

**Cons:** None significant.

**Context:** Should be done before or alongside the statistical-design TODO above — its output (which signals look weak vs. strong in backtest) may directly inform the per-signal sample-size tiers.

**Depends on / blocked by:** Nothing — can start immediately, purely a read query against existing `BacktestResult` data.

**Findings (2026-08-01):** Queried `signal_confidence` (all 26 signals — the design doc's "25" was off by one) and `backtest_results` directly.

- 21 of 26 signals are already flagged `weak_or_no_edge`, `unreliable_low_sample`, `inconsistent_across_horizons`, `decayed_edge`, `liquidity_inverted`, or `unstable_multi_dimensional` — excluded from live validation, no reason to spend the 4-6 week window re-learning what's already known.
- 5 signals are `high_confidence`. One (`rsi_14 > 70`) had never had transaction-cost-viability analysis run — added it to `compute_transaction_cost_adjusted_returns.py::TRACKED_SIGNALS` and re-ran the existing pipeline (same methodology as the other 4, not a one-off calculation). Result: clears costs at 10-day and 20-day horizons (20d adjusted return +0.956%, the strongest 20-day margin of the four cost-checked signals).
- One (`shooting_star`) explicitly failed its own cost-viability check ("negative once the estimated bid-ask spread is included... do not treat as a confirmed tradeable edge") and only turns positive when MTF-confirmed — excluded from the paper-trade scope as the raw signal.
- Final scope: **4 signals** — `rsi_14 < 30`, `close < bollinger_lower`, `rsi_14 > 70`, and `doji` (restricted to high-liquidity symbols only, per its own liquidity-stratified backtest — medium/low liquidity are flat-to-negative for this one). Expected monthly firing rates and full justification: `docs/designs/paper-trade-validation-engine.md`, "Finalized Signal Scope" section.

## CLI status check for paper-trade validation

**What:** A small script showing calls graded so far, running hit rate, and days/calls remaining in the validation window.

**Why:** Dashboard-lite visibility before there's any real dashboard (Approach B is deferred) — lets the founder check progress without querying the DB by hand.

**Pros:** Cheap to build once the ledger table exists; low risk; immediate utility during the 4-6 week private window.

**Cons:** None significant — small, isolated script.

**Context:** Surfaced during `/plan-ceo-review` (2026-08-01) expansion scan, not selected for this plan's initial scope — deferred as a fast-follow rather than blocking the validation gate's start.

**Depends on / blocked by:** Depends on the `SignalCall` model and grading job (T2/T4 in the eng-review implementation tasks) existing first.

## "Surprising miss" flagging for signal tuning

**What:** Auto-log calls that badly missed (large negative outcome vs. expectation), with the `TechnicalSignal` values at the time, as a debugging/tuning reference.

**Why:** Builds a learning log for future signal tuning without waiting for the full validation window to end.

**Pros:** Cheap addition once grading exists; useful qualitative signal alongside the quantitative hit-rate number.

**Cons:** Requires defining "surprising" (some threshold) — minor design decision deferred with this task.

**Context:** Surfaced during `/plan-ceo-review` (2026-08-01) expansion scan, deferred.

**Depends on / blocked by:** Depends on `grade_signal_calls.py` (T4) existing first.

## Discord milestone pings for validation progress

**What:** Reuse the existing Discord webhook (already used for pipeline failure alerts) to also ping on validation milestones — e.g. "500 calls graded" or "6 weeks elapsed."

**Why:** Zero new infrastructure (Discord webhook already exists per `src/notifications/discord_alert.py`); keeps the founder informed without checking manually.

**Pros:** Nearly free to build; reuses existing, working notification infra.

**Cons:** None significant.

**Context:** Surfaced during `/plan-ceo-review` (2026-08-01) expansion scan, deferred as non-blocking.

**Depends on / blocked by:** Depends on the grading job existing; can be added incrementally at any point.

## Read-only API for the signal-call ledger (B2B groundwork)

**What:** A minimal read-only API endpoint exposing `SignalCall` ledger data, usable privately by the founder at first.

**Why:** If the B2B licensing direction (Approach B from `/plan-ceo-review`, kept on roadmap alongside the chosen Approach A) is pursued later, this means the ledger already has an API shape instead of needing new migration work then.

**Pros:** Cheap groundwork now for a path explicitly kept open on the roadmap; the ledger data is the same asset either way.

**Cons:** Real effort (M, per the cherry-pick review) for a path not yet committed to — deferred rather than built speculatively now.

**Context:** Surfaced during `/plan-ceo-review` (2026-08-01) expansion scan (platform-potential candidate), not selected for the initial build — Approach A (direct-to-consumer) is the current business-model choice, B2B is roadmap-only for now.

**Depends on / blocked by:** Depends on the `SignalCall` model and ledger existing. No urgency until Approach B is actively pursued.
