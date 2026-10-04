# Earlier studies rerun on corrected prices

Rerun 2026-10-04 on the development window only (prices to 2025-01-19; every database read ran as the guarded research role). Three versions of each study are compared:

- **Before:** the published result, computed on the price and corporate-action data of 2026-10-01, before 1,012 corporate actions for delisted and suspended symbols were recovered (`docs/PRICE_INTEGRITY.md` §3).
- **Recovered:** the same code on today's data. That includes the recovered actions, 2,937 actions in the window against 2,005 before. No step exclusion is applied.
- **Corrected:** recovered data plus the corrected detector (moves measured from the NEPSE adjusted base, `docs/PRICE_INTEGRITY.md` §6). Any holding window that spans an unresolved price step is excluded.

The pre-registered hypotheses, holds, gates, folds and costs are unchanged. One input is different by construction: the variant ledger grew from 39 to 52 trials, so the ledger-level α (a diagnostic, not a gate) tightened from 0.00128 to 0.00096. Raw outputs: `docs/event_results_{recovered,corrected}.json`, `docs/broker_flow_results_{recovered,corrected}.json`, `docs/matrix_v2_corrected.json` (grade version `accuracy-v2-pi2`, 160,478 new grades; the original `accuracy-v2` grades are untouched).

## 1. Event study (`docs/EVENT_RESULTS.md`)

Abnormal return is per trade versus the same-date universe, as a mean by date. The conservative low is the gate's bound at family α = 0.00625.

| | Before: trades, abnormal, conservative low → verdict | Recovered | Corrected |
| --- | --- | --- | --- |
| E1 book-close run-up (long) | 709, +1.74%, −0.48% → **fail** (G1, G3) | 995, +2.38%, **+0.02%** → **pass** | 992, +2.34%, **−0.02%** → **fail** (G1) |
| E2 post-book-close drift (avoid) | 683, −3.68%, −5.06% → **pass** | 969, −3.67%, −4.58% → pass | 968, −3.65%, −4.56% → **pass** |
| E3 new-listing run (long) | 10, −19.4% → fail | 10, −19.6% → fail | 10, −19.6% → fail |
| E4 upper-circuit streak end (avoid) | 171, −6.58%, −9.37% → **pass** | 173, −6.38%, −9.07% → pass | 173, −6.38%, −9.07% → **pass** |
| E5 volume spike, no news (long) | 4,627, −1.42% → fail | 4,491, −1.29% → fail | 4,486, −1.28% → fail |
| E6 market-state timing | fail (G5) | fail (G5) | fail (G5) |
| E7 volume spike variant | 4,410, −2.91% → fail | 4,365, −2.60% → fail | 4,352, −2.67% → fail |
| E8 volume spike variant | 4,063, −5.55% → fail | 4,121, −4.93% → fail | 4,094, −5.02% → fail |

Event counts rose for the book-close tests (852 → 1,314 events), because delisted companies' book closes now exist: the survivorship gap noted in caveat 2 of `docs/EVENT_RESULTS.md` is partly closed. The corrected run marked 89 unresolved step sessions in the panel.

**E1 is on a knife edge, not a new finding.** With recovered actions, its conservative bound rose from −0.48% to +0.02%, enough to pass. With unresolved-step windows excluded, it fell to −0.02%. A gate that flips on 3 trades out of 992 measures nothing. E1 also still assumes the book-close announcement date rather than observing it (caveat 3 of the original report). The corrected result is the one of record: **E1 fails**.

E2 and E4 are robust to both corrections. E2's abnormal return is unchanged with 42% more events, now including delisted companies, and its bound is still far below zero.

## 2. Broker-flow evaluation (`docs/BROKER_FLOW_RESULTS.md`)

Mean excess return at 1% cost of the top portfolio versus the same-date universe, with the conservative low at family α:

| Hypothesis (feature) | Before | Recovered | Corrected | Gates passed (of 6) |
| --- | --- | --- | --- | --- |
| H1 top-5 net buy share | −0.53% (low −1.06%) | −0.54% (−1.06%) | −0.54% (−1.06%) | 1 / 1 / 1 |
| H2 buy minus sell HHI | −0.78% (−1.12%) | −0.79% (−1.13%) | −0.78% (−1.13%) | 1 / 1 / 1 |
| H3 buy HHI change | −1.11% (−1.33%) | −1.11% (−1.34%) | −1.11% (−1.33%) | 0 / 0 / 0 |
| H4 early-buyer imbalance | −0.80% (−1.02%) | −0.83% (−1.06%) | −0.83% (−1.06%) | 1 / 1 / 1 |
| H5 close vs top-5 buyer VWAP | −1.28% (−1.90%) | −1.30% (−1.93%) | −1.30% (−1.93%) | 0 / 0 / 0 |

Labelled rows: 303,997 → 301,375 → 301,272. More windows are now excluded for a bonus or right inside the window (14,414 → 20,279, from the recovered actions). Fewer are excluded for a price discontinuity (3,598 → 355), because recorded actions now explain most of those jumps. The corrected run excludes another 103 windows that span an unresolved step. The feature store (built from the floorsheet) was not rebuilt; only the labels and exclusions changed. **All five still fail.**

## 3. Situation matrix for model v0 (`docs/MATRIX_V2.md`)

| | Before (`accuracy-v2`) | Corrected (`accuracy-v2-pi2`) |
| --- | --- | --- |
| Unresolved step sessions in the window | 187 | 156 |
| Universe windows excluded, 5 / 20 / 40 / 80 / 240 sessions | 623 / 1,967 / 3,649 / 6,766 / 16,395 | 459 / 1,402 / 2,607 / 4,852 / 11,796 |
| Verdicts | 0 PASS, 38 NO EVIDENCE, 66 INSUFFICIENT SAMPLE | 0 PASS, 38 NO EVIDENCE, 66 INSUFFICIENT SAMPLE |
| v0 overall edge at 5 / 10 / 20 / 40 / 80 sessions | +2.05 / +1.97 / +1.88 / +1.86 / +0.56 points | +2.05 / +1.99 / +1.89 / +1.89 / +0.71 points |
| `new_listing` at 40: edge, plain bound, penalized bound | +8.75, +1.06, −6.44 | +8.78, +1.09, −6.37 (*K* = 520) |

No cell changed verdict. The largest edge change in any cell is 1.2 points.

## 4. Does any earlier conclusion change?

**No.**
- The event study still has exactly two passes, the avoid rules E2 and E4, and both are slightly more robust because delisted companies are now included.
- E1 briefly crossed its bound on the recovered data and fell back once unresolved steps were excluded. It stays a failure.
- All five broker-flow hypotheses still fail, with nearly identical numbers.
- The v0 matrix is still 0 PASS, and `new_listing` at 40 sessions is still the only near miss, failing the penalized bound by more than 6 points.
- The corrections changed exclusion counts by about a quarter and moved estimates by hundredths of a point. They did not change the answer to any question.
