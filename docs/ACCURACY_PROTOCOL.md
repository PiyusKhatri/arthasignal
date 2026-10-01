# Accuracy Protocol (Scorecard)

Protocol `accuracy-v1`, written on 2026-10-01 **before any scorecard result was computed**. It defines how every analysis or prediction the system makes is recorded, graded and judged, by horizon and by situation. The implementation lives in `src/scorecard/`. Constants live in `src/scorecard/spec.py`; changing any of them requires a new protocol version (`accuracy-v2`, …) and a new entry in this document. Results under one version are never re-graded under another without keeping both.

## 0. Scope and data

- **Development window for replay validation:** signal dates from 2014-06-01, with every price used (including exits) on or before **2025-01-19**. The repository holdout (2025-09-30 onward) is never read by replay. Live calls are graded only as their horizons mature.
- **Prices:** raw `daily_prices` for equities, with explicit corporate-action handling (`src/backtest/event_study.build_panel`): on the ex-session (the first session on or after the book-close date), the cash dividend net of 5% tax on pre-bonus shares, then bonus shares, then rights subscribed at par 100.
- **Replay versus live:** every call is stamped `mode = 'replay'` or `mode = 'live'`. Replay calls validate the machine and describe historical behaviour. **Only live calls, recorded before the entry session opens, count as evidence that a production model works.**

## 1. Horizons, entry, exit, costs, fills

| Class | Horizons (sessions) | Calendar span |
| --- | --- | --- |
| Short | 5, 10, 20 | within about 1 month |
| Mid | 40, 80, 120 | about 2-6 months |
| Long | 160, 240 | about 6-12 months |

A call made with data up to the close of session *t* (the **signal session**):

- **Entry:** from 2018-02-18 (the first session with real opening prices), at the **open of session *t*+1**. Before 2018-02-18 the stored open equals the previous close, so entry is at the **close of session *t*+1** and every price in that call is a close.
- **Exit:** at the **open of session *t*+1+*h*** (the open after the horizon), or its close for calls entered before 2018-02-18. All session counts use the market session calendar (every date with at least one price row).
- **Costs:** 0.5%, 1.0% and 1.5% round trip, subtracted from the gross return. **1.0% is primary** for the definition of a correct call.
- **No buy on a locked upper circuit:** if the stock does not trade in the entry session, or every trade in it is at the upper limit (low ≥ previous close × (1 + limit − 0.5 pt); limit 10%, 15% from 2026-04-20), the call is **UNFILLED**. An unfilled call is graded **incorrect** with net return 0 and lowers coverage. It is never voided.
- **Blocked sells count as losses:** if the stock does not trade in the exit session, or every trade in it is at the lower limit, the position is sold at the first later session where it trades without a lower lock (up to 20 sessions), otherwise at the last available price (**STRANDED**). Either way the call is graded **incorrect**, whatever the return, and the realized return enters expectancy. It is never voided.
- **Data errors:** if the holding window contains a corrupt price step (a one-session move beyond the circuit limit + 2 points after corporate-action adjustment, usually an unrecorded split or bonus), the call is graded **DATA_ERROR**. These calls are counted and reported per strategy. If more than 1% of a cell's calls are data errors, the cell's verdict cannot be PASS.
- **Not yet matured:** a call whose exit session is after the last available price is **PENDING**, not graded.

## 2. What counts as a correct call

Benchmarks use the **same-date universe**: every equity that traded in signal session *t*, graded with exactly the same entry, exit and fill rules (unfilled universe stocks are excluded from the medians and means, and blocked ones enter at their realized return).

| Class | Correct (at 1.0% cost) |
| --- | --- |
| Short (5/10/20) | net return > 0 **and** gross return > same-date universe median gross return |
| Mid (40/80/120) | net return > 0 **and** gross > universe median **and** gross > same-date median of the stock's sector (`companies.sector`) |
| Long (160/240) | gross return > same-date equal-weight universe mean gross return. For strategies that rank at least 10 stocks on a date, also report the **top-minus-bottom quintile spread** of gross returns among the ranked stocks |

Comparing gross against gross is the same as comparing net against net under a common cost.

**Baselines**, computed for every call from the same-date universe:

- **Same-date random stock:** the share of universe stocks on that date that would be correct under the same definition. This is the baseline win rate; its mean over a cell's calls is the cell's baseline.
- **Equal-weight universe:** mean gross return of the same-date universe.
- **NEPSE buy-and-hold:** NEPSE Index over the same entry and exit sessions (open to open from 2018-02-18, close to close before).

## 3. Metrics

For every strategy version × horizon × situation cell:

- **Calls; filled share (coverage);** unfilled, blocked, stranded, data-error and pending counts.
- **Net win rate** (correct / graded) and **baseline win rate**; **edge** = win rate − baseline (percentage points).
- **Expectancy** at 0.5/1.0/1.5%: mean net return per graded call (unfilled = 0). Also excess over the equal-weight universe and over NEPSE.
- **Brier score** of the call's stated probability of being correct (`probability`; a strategy that states none is recorded at 0.5) against the outcome. **Reliability buckets:** deciles of stated probability, each with calls, mean stated probability and observed win rate. **Calibration error (ECE)** = call-weighted mean |stated − observed| over buckets with ≥ 30 calls.
- **Clustered intervals:** calls are clustered into **non-overlapping windows of the horizon**: cluster = ⌊signal-session index / (*h*+1)⌋. Windows that share no holding sessions are treated as independent. The win rate's one-sided lower bound uses the mean and standard error of cluster means (`src/backtest/stats.clustered_mean_interval`). Many calls in one window count as roughly one observation of that window.
- **Distinct dates** and **independent windows** per cell.
- **Multiple testing:** each strategy version is registered in `backtest_variant_trials` (family `scorecard_accuracy_v1`). The penalty uses *K* = (strategy versions registered in the family) × (matrix cells evaluated per version); the one-sided level is α = 0.10 / *K*. The plain 90% bound is reported next to it.

### Power table, 2014-06-01 to 2025-01-19 (2,435 sessions)

Independent windows = ⌊2,435 / (*h*+1)⌋. The required count is the worst case of one call per window (Bernoulli). With many calls per window the cluster SE can be smaller, but it cannot be relied on, because stocks in the same window move together.

| Horizon | Independent windows | Prove 60% vs 50% (90% one-sided, needs 39) | Gate: 62% with lower bound ≥ 55% (needs 79) | Prove 60% with lower bound ≥ 55% (needs 158) | Gate with the 104-cell penalty (α = 0.10/104, z = 3.10, needs 463) |
| ---: | ---: | :-: | :-: | :-: | :-: |
| 5 | 405 | yes | yes | yes | **no** |
| 10 | 221 | yes | yes | yes | no |
| 20 | 115 | yes | yes | **no** | no |
| 40 | 59 | yes | **no** | no | no |
| 80 | 30 | **no** | no | no | no |
| 120 | 20 | no | no | no | no |
| 160 | 15 | no | no | no | no |
| 240 | 10 | no | no | no | no |

**Plainly:** over this 10.6-year window a "60% accurate" claim can be supported against a coin flip only at horizons of 5-40 sessions, and with the gate's 55% lower bound only at 5, 10 and 20 sessions. **At 80 sessions and beyond, no amount of cross-sectional calls in this history can prove 60%**, because there are only 30 or fewer independent windows. If all 104 cells are tested, the penalty pushes the requirement to 463 windows, more than any horizon has, so a PASS needs either far fewer cells tested or live data accumulating over years. Sub-cells (situations) have fewer windows still.

## 4. Horizon × situation matrix

Situations are labelled for each call **only from data available at the close of the signal session *t*** (event-table definitions in `src/backtest/event_tables.py`, label code in `src/scorecard/situations.py`). A call can carry several labels. `all` is always present.

| Situation | Label | Definition at close of *t* |
| --- | --- | --- |
| Market state | `market_bull` | NEPSE close > its 200-session SMA, 50-session SMA > 200-session SMA, and not overheated |
| | `market_overheated` | NEPSE close ≥ 1.20 × 200-session SMA |
| | `market_bear` | NEPSE close < 200-session SMA and 50-session SMA < 200-session SMA |
| | `market_sideways` | the 200-session SMA exists and none of the above |
| Book-close proximity | `pre_book_close` | the stock has a book-close ex-session in *t*+1 … *t*+15 (assumes the date was announced by *t*; announcement dates are not stored, see `docs/DATA_PLAN.md`) |
| | `post_book_close` | a book-close ex-session in *t*−9 … *t* |
| New listing | `new_listing` | the stock's first price is within the last 60 sessions, after 2014-07-01, and the symbol is not the product of a merger |
| Circuit-lock aftermath | `post_upper_circuit` | an upper-limit close streak of ≥ 2 sessions that ended within *t*−9 … *t* |
| | `post_lower_circuit` | a lower-limit close in *t*−4 … *t* |
| Volume anomaly | `volume_anomaly` | volume in *t* ≥ 5 × median of the previous 60 sessions (≥ 40 traded) |
| Interest-rate change | `rate_rising` / `rate_falling` | the last published T-bill rate (45-day publication lag, from 2016-11-03) differs from the value known 60 sessions earlier by ≥ +1.0 / ≤ −1.0 point. NRB policy-rate announcements are not yet stored; this is a proxy until `policy_events` exists |

Each cell (strategy version × horizon × situation) reports: calls, distinct dates, independent windows, net win rate, baseline, edge, one-sided 90% and penalized lower bounds, expectancy at three costs, excess over the universe and over NEPSE, Brier, ECE, fold signs and a **verdict**:

- **PASS** only if **all** of the following hold:
  1. Net win rate (1.0% cost) ≥ **62%**.
  2. One-sided lower bound on the win rate at the penalized level (α = 0.10/*K*) ≥ **55%**. The plain 90% bound must also be ≥ 55%.
  3. Edge over the same-date random-stock baseline ≥ **8 points**.
  4. Expectancy **> 0 at 0.5%, 1.0% and 1.5%**.
  5. Edge > 0 in **at least 3 of the 4 time folds** (4 equal blocks of signal sessions; a fold with no calls counts as not positive).
  6. **≥ 150 graded calls on ≥ 60 distinct signal dates.**
  7. Calibration within tolerance: ECE ≤ **0.05** and |mean stated probability − observed win rate| ≤ **0.05** over the cell.
  8. Data errors ≤ 1% of calls, and the look-ahead audit (section 5) passed for this strategy version.
- **INSUFFICIENT SAMPLE** if fewer than 150 graded calls or fewer than 60 distinct dates. Below that, the cell cannot pass whatever its numbers.
- **NO EVIDENCE** otherwise.

## 5. Look-ahead audit, rolling monitoring, kill criteria, refine loop

**Look-ahead audit (every strategy version, before any verdict).** For a fixed sample of 40 signal dates, the strategy is re-run on a copy of the data with everything after the close of *t* removed. Its calls must be identical to the calls it made with the full data. Any difference marks the version **LEAKY**: all its cells are blocked from PASS and the report says so. Separately, any cell with a win rate ≥ 80% and ≥ 150 calls is flagged **IMPLAUSIBLE** for manual review.

**Rolling monitoring (live mode; also shown for replay).** Per strategy version and horizon, over the most recent 60 graded calls and the most recent 120 signal sessions:

- edge over baseline with its plain 90% lower bound;
- expectancy at 1.0%;
- Brier score against the always-0.5 score of 0.25.

**Kill criteria.** A strategy version × horizon is **suspended** (no new user-facing calls) when any of these holds:

1. Rolling edge lower bound < 0 for two consecutive monitoring points 20 sessions apart.
2. Rolling expectancy at 1.0% < 0 with an upper bound < 0.
3. Rolling Brier > 0.25 (worse than claiming nothing) over ≥ 60 graded calls.
4. The look-ahead audit fails, or data errors exceed 1%.

A suspended version can come back only as a new version, registered as a new variant.

**Refine loop: failure causes for wrong calls.** Every incorrect graded call gets exactly one cause, in this priority order:

| Cause | Rule |
| --- | --- |
| `liquidity` | unfilled, blocked exit or stranded |
| `circuit` | a lower-limit close inside the holding window |
| `news` | a corporate-action ex-session or a volume ≥ 5× its 60-session median inside the holding window (a proxy until text data exists) |
| `market` | the same-date universe mean return < 0 and the stock's gross return ≥ universe mean − 2 points (it fell with the market) |
| `sector` | the sector median < the universe median and the stock's gross return ≥ sector median − 2 points |
| `model` | none of the above |

The refine loop reads the cause mix per cell. A model change may target only a cause share that is materially larger than the same cause share for the no-edge baselines in the same cell (`docs/SCORECARD_BASELINES.md`), and every change is a new version.

## 6. What this protocol forbids

- Re-grading or editing a call: the ledger rejects UPDATE and DELETE at the database level, and a new grading version inserts new rows.
- Calling a replay PASS a production result.
- Dropping blocked, stranded or unfilled calls.
- Changing horizons, the correctness definitions, gates, situations or costs without a new protocol version.
- Training a predictive model inside this machine.
