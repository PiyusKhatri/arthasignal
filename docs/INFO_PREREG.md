# Earnings-information hypotheses: pre-registration (`info-prereg-v1`)

**Declared:** 2026-10-04 09:41:54 Nepal time, before any return, growth distribution or event count conditional on outcomes was computed. Only collection counts (`docs/INFORMATION_SURVEY.md` §4) were known. **Code:** `src/scorecard/info_spec.py`. **Registry:** `backtest_variant_trials`, family `info_prereg_v1`, six rows (see `docs/PHASE_LOG.md`, Phase Q6).

Price-only approaches have produced nothing (`docs/MATRIX_V2.md`, `docs/RERUN_CORRECTED.md`). These six hypotheses test whether dated earnings and dividend information adds edge. There are no more than six tests, and nothing is added after results are seen. Anything new goes only in a clearly labelled exploratory section of `docs/INFO_RESULTS.md`.

## Data (publication-timestamped only)

| Input | Table | Point-in-time column |
| --- | --- | --- |
| Quarterly report with headline net profit | `quarterly_report_announcements` (Sharesansar rows with `net_profit`, not corrections) | `published_date`. When Merolagani has the same symbol, fiscal year and quarter, the **later** of the two first-publication dates is used |
| Dividend declaration (cash %, bonus %, book close) | `dividend_declarations` | `announcement_date` |
| Prices, sectors, universe | as in the scorecard (`src/scorecard/grading`, corrected detector, unresolved-step windows excluded) | session dates |

No snapshot fundamentals (`fundamentals`, `quarterly_report_figures`) are used: they carry no publication history, and Merolagani rescales EPS retroactively (`docs/EPS_RECONCILIATION.md`).

## Timing (knowledge and entry)

- **Knowledge session *t*:** the first trading session whose date is **strictly after** the publication date. No source records the time of day, so a report dated *d* may appear after the close of *d*. By the close of the next session it is public.
- **Entry:** next open after *t* (protocol v2: the open of *t*+1 from 2018-02-18, the close of *t*+1 before). **Exit:** at the open after *h* sessions. Unfilled, blocked and stranded calls are graded wrong.
- **Features use only rows published before *t***. The quartile threshold for *t* is computed from growth values whose knowledge session lies in the 250 sessions **before** *t* (at least 40 values, else no call).

## Features

- **YoY growth** of a report (symbol, fiscal year, quarter) = (NP − NP₋₁) / |NP₋₁|. NP₋₁ is the same quarter of the previous fiscal year, from a report published earlier. It is computed only when |NP₋₁| ≥ Rs 1 million.
- **Previous declared total** for a dividend declaration = cash % + bonus % of the symbol's most recent earlier declaration.

## The six hypotheses (all long, all graded as buy calls)

| ID | Event | Selection at *t* | Horizon (sessions) |
| --- | --- | --- | ---: |
| I1 | Quarterly report | YoY growth ≥ the trailing 75th percentile | 20 |
| I2 | Quarterly report | same selection as I1 | 40 |
| I3 | Quarterly report | NP > 0 after NP₋₁ < 0 (turnaround) | 20 |
| I4 | Dividend declaration | cash + bonus > 0 and ≥ the previous declared total | 10 |
| I5 | Dividend declaration | bonus > 0 and book close ≥ 12 sessions after *t* (exit falls before book close) | 10 |
| I6 | Quarterly report, Commercial Banks only | YoY growth ≥ the all-sector trailing 75th percentile | 20 |

Each hypothesis is evaluated **only at its listed horizon**. Other horizons are not reported as tests. A symbol may be called more than once only for separate events.

**Diagnostic controls, not tests:** every report with a computable YoY growth, held 20 sessions (control for I1, I3 and I6), and every dividend declaration, held 10 sessions (control for I4 and I5). These show whether the selection adds anything over simply trading the event.

## Window, folds, baselines, gate

- **Development window:** signal sessions 2014-06-01 to 2025-01-19. Exits must fall on or before 2025-01-19. Dividend announcement dates exist only from 2018, so I4 and I5 effectively run 2018-2025.
- **Baseline:** the same-date universe baseline share of protocol v2, meaning the share of all universe stocks on that date that would be correct under the same rule and fills. Edge = mean(correct − baseline share).
- **Folds:** four equal blocks of signal sessions (v2).
- **Gate:** protocol `accuracy-v2`, unchanged:
  1. edge ≥ +8 points;
  2. lower bound on clustered means > 0 at both the plain 90% level and the penalized level α = 0.10 / *K*, with *K* = versions in the family × 104 = 624;
  3. expectancy > 0 at 0.5%, 1.0% and 1.5% cost, and excess over the universe mean at 1.0% > 0;
  4. edge > 0 in at least 3 of 4 folds;
  5. sample of ≥ 150 graded calls, ≥ 60 distinct dates and the v2 minimum independent windows (40 at 10 and 20 sessions, 30 at 40);
  6. calibration: no probabilities are stated, so the cell is "uncalibrated";
  7. look-ahead audit.
- **Verdicts:** PASS, NO EVIDENCE or INSUFFICIENT SAMPLE.
- **Look-ahead audit:** each hypothesis's selections up to *t* must not change when every price, report and declaration after *t* is removed.

## What would count

A hypothesis counts as supported only if it PASSes. A PASS on the development window would still only be a candidate for live tracking, never a claim. The events-per-year table in the results will show whether any test had enough independent events to mean anything.
