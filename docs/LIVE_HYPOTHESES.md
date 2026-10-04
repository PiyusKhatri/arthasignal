# Live hypotheses for model v0

**Declared:** 2026-10-04 01:04:22 Nepal time (2026-10-03 19:19:22 UTC), on top of commit `9dff616`, before any live call was written and before any data after 2025-09-29 was used.

**Model:** `model_v0` version `v0` (`scorecard_models` id 1, parameters hash `46ba8c8c7f5090ae09eba3824d4ab261fae6f618e92bdf0b1ff72d80ab4875f0`, description in `src/scorecard/model_v0.py`). **Code:** `src/scorecard/hypotheses.py`. **Registry:** `backtest_variant_trials`, family `live_hypotheses_v0`, one row per hypothesis (fingerprints below).

Exactly four hypotheses will be tracked live. Nothing else from v0, from the situation matrix, or from earlier studies is a live hypothesis. A cell that is not listed here cannot be promoted to a claim from live data without a new declaration made before its data exist.

## The four hypotheses

| ID | Component | Side | Rule (point-in-time, at the close of *t*) | Horizon | Status before live |
| --- | --- | --- | --- | ---: | --- |
| H1 | Avoid rule E2 | avoid | bonus book-close ex-session in the last 20 sessions | 20 | E2 passed its pre-registered event test (2014-06 to 2025-01), with the caveats in `docs/EVENT_RESULTS.md` |
| H2 | Avoid rule E4 | avoid | upper-circuit close streak of ≥ 3 ended in the last 20 sessions | 20 | E4 passed its pre-registered event test, with the same caveats |
| H3 | Momentum tilt | buy | v0 calls that are not new listings: top 10 by 20-session total return, no calls in bear states | 5, 10, 20 | NO EVIDENCE in every replay cell (edge +1.9 to +2.1 points) |
| H4 | New-listing tilt | buy | v0 calls labelled `new_listing` | 40 | NO EVIDENCE in replay (edge +8.75 points, plain bound +1.1, penalized bound −6.4) |

**H4 was found after looking at results.** It is the cell that came closest in `docs/MATRIX_V2.md`, which was computed before this declaration. Choosing it because it looked best is a selection on the outcome, so the replay number is **not evidence**: it is the reason for a hypothesis. Only live calls made after this declaration count toward H4. The same applies, less strongly, to H3 (chosen knowing momentum's replay edge) and to H1 and H2 (whose event tests had been run). None of the four has evidence for live use today.

## Metrics and gate (protocol `accuracy-v2`, unchanged)

Each hypothesis is graded per horizon, on live calls only (`mode = 'live'`), under `docs/ACCURACY_PROTOCOL.md` v2:

- **Correct call** for a buy (H3, H4): short horizons need net > 0 and gross > the same-date universe median; at 40 sessions the gross return must also beat the same-date sector median. Next-open entry, exit at the open after the horizon. Unfilled, blocked and stranded calls are graded wrong.
- **Avoid calls** (H1, H2) mirror the buy definition: an avoid observation is correct when the stock would have been an *incorrect* buy under the same rule, fills and costs. Its baseline is 1 − the same-date buy baseline share, so its edge is (buy baseline share − buy correctness). Every stock that meets the rule on a live date is one observation.
- **Edge** = mean (correct − same-date baseline share). Clusters are non-overlapping horizon windows.

**PASS needs all of:**

1. edge ≥ +8 points;
2. edge lower bound > 0 at the plain one-sided 90% level **and** at the penalized level α = 0.10 / *K*, with *K* = versions in the `scorecard_accuracy_v2` family × 104, as defined in v2 (currently 5 × 104 = 520);
3. expectancy > 0 at 0.5%, 1.0% and 1.5% round-trip cost, and excess expectancy over the same-date universe mean > 0 at 1.0% (for H1 and H2 the sign is reversed: avoided stocks must trail the universe);
4. edge > 0 in at least 3 of the 4 time folds of the live period;
5. sample: ≥ 150 graded calls, ≥ 60 distinct signal dates, and at least the minimum independent windows (40 at 5-20 sessions, 30 at 40);
6. calibration: v0 states no probability, so this gate is passed as "uncalibrated" and no probability may be shown;
7. the look-ahead audit passes for v0.

Kill rules (v2 section 5) apply from the first monitoring point: suspend when the rolling edge lower bound is < 0 at two consecutive points, or when excess expectancy is < 0 with an upper bound < 0. Verdicts are PASS, NO EVIDENCE or INSUFFICIENT SAMPLE.

## Minimum live sample before any claim

No claim may be made about any hypothesis until gate 5 is met on live calls. The binding constraint is independent windows, not call count. NEPSE has about 229 sessions a year (2,435 sessions over 2014-06-01 to 2025-01-19). The replay shares below say how often v0 had a call in a window.

| Hypothesis | Horizon | Minimum (calls / dates / windows) | Binding constraint | Earliest possible claim |
| --- | ---: | --- | --- | --- |
| H3 momentum | 5 | 150 / 60 / 40 | windows; v0 had calls in 69% of 5-session windows | ≈ 354 sessions, about **1.5 years** |
| H3 momentum | 10 | 150 / 60 / 40 | windows (72%) | ≈ 626 sessions, about **2.7 years** |
| H3 momentum | 20 | 150 / 60 / 40 | windows (73%) | ≈ 1,171 sessions, about **5.1 years** |
| H4 new listing | 40 | 150 / 60 / 30 | windows (80% coverage in replay) | ≈ 1,584 sessions, about **6.9 years** |
| H1 avoid E2 | 20 | 150 / 60 / 40 | windows: ≥ 40 × 21 sessions; calls at about 0.28 a session | ≥ 840 sessions, **3.7 years or more** |
| H2 avoid E4 | 20 | 150 / 60 / 40 | calls: about 0.07 a session (171 events in 2,435 sessions) | ≈ 2,136 sessions, about **9.3 years** |

These are lower bounds that assume every window qualifies and nothing is excluded. **Within the next year, live data can support no claim on any of the four.** H3 at 5 sessions is the earliest that can, in about 1.5 years. H2 may never reach a claim at its event rate.

## What live tracking needs that does not exist yet

- The live writer records v0's buy calls (H3, H4). It does **not** yet record the stocks excluded by the avoid rules. For H1 and H2 to be graded from frozen records, the writer must also write avoid observations to the ledger before the next open. Until that is built, H1 and H2 are declared but not being tracked.
- The `new_listing` and momentum split comes from the situation labels stored on each live call, so H3 and H4 are separable from the ledger as it is.

## Registry rows

Registered in `backtest_variant_trials` (family `live_hypotheses_v0`) by `python -m src.scorecard.hypotheses`; the fingerprints are listed in `docs/PHASE_LOG.md` (Phase Q2).


## Amendment, 2026-10-04 11:30:26 NPT (protocol v2.1, model v0.1)

Before any live v0 call was written (0 live rows), the following changed:
- the call deadline moved to the next NEPSE session open;
- promoter shares were removed from the equity universe.

The model rules are identical, but its universe changed, so the live writer now runs **model v0.1** (`scorecard_models` gets a new row when the writer first runs). H1-H4 are re-registered for v0.1 in `live_hypotheses_v0` by `python -m src.scorecard.hypotheses --amend`. The gate is unchanged apart from the protocol label. The minimum-sample estimates above came from v0's universe.
