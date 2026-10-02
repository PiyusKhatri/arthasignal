# Accuracy Protocol (Scorecard) v2

Protocol `accuracy-v2`, adopted on 2026-10-02. It replaces `accuracy-v1` (`git show d3ec7f8:docs/ACCURACY_PROTOCOL.md`). Grades under v1 stay in the ledger with `grade_version = 'accuracy-v1'`, and v2 grades are inserted alongside as `'accuracy-v2'`. Neither is edited. Code: `src/scorecard/` (v2 rules in `src/scorecard/v2.py`).

## Changelog: why v2

The v1 baselines (`docs/SCORECARD_BASELINES.md`) showed the v1 gate measuring the wrong things:

| v1 rule | What the no-edge replay showed | v2 rule |
| --- | --- | --- |
| Absolute win rate ≥ 62% | A random stock is "correct" only 23-37% of the time. 62% would require +25 to +38 points of edge; perfect 20-session foresight reached only 55% at 80 sessions | **Edge over the same-date baseline ≥ +8 points**, with a clustered lower bound above zero |
| Expectancy > 0 at all costs | Automatic for no-edge strategies at ≥ 20 sessions in a rising decade (+1.5% to +28%) | Kept as a necessary condition, **plus excess expectancy over the same-date universe mean > 0** at 1% cost |
| Calibration against stated probability, default 0.5 | An honest no-edge strategy stating 0.5 fails calibration (true rate 24-37%) | **Calibration judged against the real baseline:** a stated probability must be calibrated (ECE ≤ 0.05) **and** have a Brier score no worse than the baseline forecast (the same-date baseline share). A call may state no probability; it is then graded on edge only and may not show a probability to users |
| Brier kill rule "Brier > 0.25" | Can never fire for a 0.5 claimer (Brier is exactly 0.25) | **Kill when rolling Brier > rolling Brier of the baseline forecast** on the same calls (a test shows it fires) |
| Failure causes by "any event in the window" | Beyond 40 sessions almost every window contains an event, so `news`/`circuit` absorbed 60-80% | **Magnitude decomposition:** market, sector and idiosyncratic components of the shortfall; event causes only when ≥ 50% of the idiosyncratic loss happened on event days |
| Data errors ≤ 1% as a gate | Unrecorded corporate actions made 1.3-16% of windows "data errors" at ≥ 20 sessions | Unresolved price steps are detected and windows spanning them are **excluded** (`docs/PRICE_INTEGRITY.md`), counted and reported; a warning above 10% |
| Sample: 150 calls, 60 dates | Silent on how many independent windows a horizon has | **Minimum independent windows per horizon** (below) |

Unchanged from v1: horizons, entry and exit rules, costs, fills (unfilled and blocked counted wrong, never voided), the definition of a correct call per horizon class, situations, the look-ahead audit, folds and replay-versus-live.

## 1. Horizons, entry, exit, costs, fills (unchanged)

Short 5/10/20, mid 40/80/120, long 160/240 sessions. Entry at the open of session *t*+1 from 2018-02-18, at the close of *t*+1 before. Exit at the open (close before 2018-02-18) of *t*+1+*h*. Costs 0.5/1.0/1.5% round trip, 1.0% primary. No buy when the entry session does not trade or every trade is at the upper limit (UNFILLED, graded wrong, return 0). A blocked or stranded sell is graded wrong at its realized return.

**New in v2: excluded windows.** A window whose entry-to-exit span contains an **unresolved price step** is marked `data_error` and excluded from every metric. An unresolved step is a move beyond the circuit band + 0.5 point that no recorded corporate action or ≥ 20-session halt explains, or where the recorded action does not reconcile the move (`src/backtest/price_integrity.py`). Exclusion counts are reported per cell.

## 2. Correct call (unchanged) and baselines

Short: net > 0 **and** gross > same-date universe median. Mid: also > same-date sector median. Long: gross > same-date universe mean (plus the top-minus-bottom spread when ≥ 10 scored calls exist on a date and the scores vary).

**Same-date baseline share** = the share of all universe stocks on that signal date that would be correct under the same rule and fills. It is stored with each grade and is both the benchmark for edge and the baseline probability forecast. NEPSE buy-and-hold over the same sessions is reported.

## 3. Metrics

Per strategy version × horizon × situation cell:
- calls and excluded windows;
- graded calls, distinct dates and independent windows (cluster = ⌊signal-session index / (*h*+1)⌋);
- win rate, baseline and **edge** (mean of correct − baseline share per call);
- the edge's one-sided lower bound at 90% and at the penalized level α = 0.10 / *K*, with *K* = versions in the family × 104 cells, both computed on cluster means;
- expectancy at the three costs, and excess expectancy over the universe mean at 1%;
- calibration (Brier, baseline-forecast Brier, Brier skill, ECE, reliability buckets);
- fold edges and failure-cause shares.

## 4. Gate (PASS needs all of these)

1. **Edge ≥ +8 points.**
2. **Edge lower bound > 0**, at both the plain one-sided 90% level and the penalized level.
3. **Expectancy > 0 at 0.5%, 1.0% and 1.5%**, and **excess expectancy over the same-date universe mean > 0 at 1.0%**.
4. **Edge > 0 in at least 3 of the 4 time folds** (4 equal blocks of signal sessions).
5. **Sample:** ≥ 150 graded calls, ≥ 60 distinct signal dates, and at least the independent windows below.
6. **Calibration:** if probabilities are stated, ECE ≤ 0.05, |mean stated − observed| ≤ 0.05 and Brier ≤ the baseline-forecast Brier. If none are stated, this gate is passed, but the cell is marked "uncalibrated" and no probability may be shown to users.
7. **Look-ahead audit passed** for the strategy version.

Verdicts: **INSUFFICIENT SAMPLE** when gate 5 fails, **PASS** when all pass, **NO EVIDENCE** otherwise.

### Minimum independent windows, and which horizons can ever support a claim (2014-06-01 to 2025-01-19, 2,435 sessions)

| Horizon | Windows in history | Minimum required | Can a claim ever be supported? |
| ---: | ---: | ---: | --- |
| 5 | 405 | 40 | yes |
| 10 | 221 | 40 | yes |
| 20 | 115 | 40 | yes |
| 40 | 59 | 30 | yes, if calls fall in at least half the windows |
| 80 | 30 | 25 | barely: calls are needed in 25 of the 30 windows |
| 120 | 20 | 25 | **no** |
| 160 | 15 | 25 | **no** |
| 240 | 10 | 25 | **no** |

**Plainly: no claim at 120, 160 or 240 sessions can be supported by this history.** At 80 sessions a claim is possible only for a strategy active in nearly every window. Long-horizon claims need years of live data. Situation cells have the same or fewer windows.

## 5. Monitoring, kill criteria, failure causes

Rolling, over the last 60 matured calls at monitoring points every 20 sessions:
- edge and its plain lower bound;
- excess expectancy over the universe at 1% and its upper bound;
- Brier against the baseline-forecast Brier, on calls with stated probabilities.

**Suspend** a version × horizon when any of these holds:
- the edge lower bound is < 0 at two consecutive points;
- excess expectancy < 0 with an upper bound < 0;
- rolling Brier > rolling baseline-forecast Brier;
- the audit fails.

A suspended version returns only as a new version.

**Failure causes (wrong graded calls), in order:**

- `liquidity`: unfilled, blocked or stranded.

Otherwise the shortfall is decomposed in log terms into three components:
- market: the same-date universe mean, if negative;
- sector: the sector median − universe median, if negative;
- idiosyncratic: the stock − sector median, if negative.

The cause is the most negative component:
- `market`, or
- `sector`, or, for the idiosyncratic component:
  - `circuit` if at least half of it happened on the stock's lower-limit-close days inside the window;
  - `news` if at least half happened on corporate-action or volume-spike (≥ 5×) days;
  - `model` otherwise.

A call that is wrong with no negative component, such as a small gain below the cost or median threshold, is `model`.

## 6. What this protocol forbids (unchanged)

Editing or deleting ledger rows; calling a replay PASS a production result; dropping blocked, stranded or unfilled calls; changing rules without a new version; training a predictive model inside this machine; reading the holdout (2025-09-30 onward) for any evaluation.
