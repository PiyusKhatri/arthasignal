# Paper-bot league v1

> **Amended 2026-10-04 11:30:26 NPT (protocol v2.1):** all six bots are now version **b2**. The rules and risk limits are identical to b1. What changed: the call deadline is the next NEPSE session open (`docs/ACCURACY_PROTOCOL.md` v2.1), and promoter shares are no longer in the universe. b1 never wrote a call and is retired. The b2 rows are registered in `paper_bot_league_v1` and `scorecard_accuracy_v2`. The coverage and minimum-sample table below was measured on b1's universe.


**Declared:** 2026-10-04 10:35:04 Nepal time (04:50:04 UTC), before any league call was written and before the league read any data after 2025-09-29. **Code:** `src/league/` (`bots.py`, `run.py`, `leaderboard.py`, `declare.py`). **Registry:** `scorecard_models` (one immutable row per bot) and `backtest_variant_trials`. Each bot appears twice there: in family `paper_bot_league_v1` (the declaration) and in `scorecard_accuracy_v2` (which raises *K* for everyone). The fingerprints are listed in `docs/PHASE_LOG.md` (Phase B).

Six independent paper-trading bots run side by side on the same ledger and the same scorecard. Each is frozen. Changing anything, including a risk rule, makes a new version (`b2`), which starts its record from zero. **No bot has evidence today.** Every rule below comes from earlier work, and the replay numbers that motivated some of them are not evidence (see `docs/LIVE_HYPOTHESES.md`).

## The bots

| Bot | Version | Side | Rule (point-in-time, at the close of *t*) | Primary horizons | Source |
| --- | --- | --- | --- | --- | --- |
| `bot_avoid_e2e4` | b1 | avoid | Every equity with a bonus book-close ex-session in the last 20 sessions (E2), or whose upper-circuit close streak of ≥ 3 ended in the last 20 sessions (E4). One observation per stock and date | 20 | E2 and E4 passed their pre-registered event tests |
| `bot_momentum` | b1 | buy | Top 10 established equities (not new listings) by 20-session total return. Abstains in the bear state. **No avoid filter** | 5, 10, 20 | Momentum tilt (H3); replay NO EVIDENCE at +1.9 to +2.1 points |
| `bot_new_listing` | b1 | buy | Top 5 new listings (first price within 60 sessions, mergers excluded) by 20-session return. Abstains in the bear state. No avoid filter | 40 | H4; chosen after seeing replay results, so a hypothesis only |
| `bot_ranker_spec` | b1 | buy | Fixed-weight cross-sectional ranker with **no fitting**: the mean percentile rank of (a) 120-session momentum skipping the last 5 sessions, (b) 5-session reversal, (c) low 60-session volatility and (d) 60-session median turnover. Top 10 | 5, 10, 20 | Momentum, liquidity and volatility are the dominant ML predictors (Gu, Kelly and Xiu 2020). A trained LightGBM ranker comes later, as a new version trained walk-forward |
| `bot_market_timer` | b1 | buy | On when the NEPSE state is bull or sideways, at least 50% of traded equities close above their own 50-session average, and NEPSE's 20-session return is > 0. When on, calls the 10 most liquid eligible equities (60-session median turnover) | 5, 10, 20 | Regime gate (`docs/RESEARCH_PLAN.md` idea 3) |
| `bot_combined` | b1 | buy | Only when the timer is on. Candidates are the top 20 by momentum score plus the top 20 by ranker score, minus every E2/E4 hit, ranked by the mean of the two percentile ranks. Top 10 | 5, 10, 20 | First, simplest version of the meta-system (vote, no fitting) |

**About the timer:** v2 measures edge against the *same-date* baseline, so pure timing earns no edge by construction. Its effect shows in expectancy, excess expectancy and drawdown, and through `bot_combined`, which uses it as a gate. Its liquid basket is graded like any other buy call.

**Independence:** the momentum, new-listing, ranker and timer bots do not use the avoid filter. That way the avoid bot's value can be seen separately, and the combined bot shows whether stacking them helps.

## Risk rules (all buy bots; frozen in `RISK`)

- Equities only (`companies.instrument_type = 'Equity'`), valid symbols only, quarantined symbols skipped.
- Liquidity:
  - traded on at least 50 of the last 60 sessions;
  - 60-session median turnover of at least Rs 2,000,000;
  - no unresolved price step in the last 120 sessions.
- No call on a stock that closed at its upper circuit limit on the signal day, since it would probably be unfilled.
- At most 10 calls a day and at most 3 per sector (`bot_new_listing`: 5 a day).
- Paper sizing is equal weight: each call is 1/10 of paper capital.
- **Kill rule:** a bot stops writing calls once v2's rolling monitor flags *suspend* at its primary horizons:
  - the edge lower bound is < 0 at two consecutive points; or
  - excess expectancy is < 0 with an upper bound < 0. For the avoid bot this is not used, because its stocks are *supposed* to trail; or
  - Brier is worse than the baseline forecast.

  A suspended bot returns only as a new version.
- The avoid bot applies only the quarantine and equity filters. Every stock meeting the rule is an observation.

## Grading, metrics and the daily leaderboard

- Calls are written as `mode = 'live'` rows in `scorecard_calls`, with strategy = bot name, model version = bot version, a feature hash and situation labels. Writes happen before 11:00 NPT on the next day: the writer refuses later, and the table's CHECK rejects late rows. Writes are idempotent under an advisory lock.
- Calls are graded under `accuracy-v2` into `scorecard_grades` as they mature. Nothing is edited (append-only triggers).
- Avoid observations are graded as buys and mirrored, as in `docs/LIVE_HYPOTHESES.md`:
  - correct when the stock would have been an incorrect buy;
  - baseline = 1 − the buy baseline;
  - edge = buy baseline − buy correctness;
  - the expectancy gate becomes "the avoided stocks trail the universe at 1% cost".
- The leaderboard runs every day for 5, 10, 20 and 40 sessions, ranked by edge, and is stored append-only in `league_leaderboard`. Columns:
  - graded calls, win rate, same-date baseline and edge;
  - the plain and penalized lower bounds;
  - expectancy at 1%, and excess expectancy over the universe mean;
  - calibration ("uncalibrated": no bot states a probability, so none may be shown);
  - **drawdown**: the maximum drawdown of a non-overlapping cohort equity curve. It takes the first signal date, then the next date at least *h* + 1 sessions later, and so on, with each cohort's mean net return at 1% cost;
  - **coverage**: the share of live sessions with calls, and calls per session;
  - windows, folds, gates and the v2 verdict.

  A Markdown copy goes to `logs/league/`.
- *K* for the penalized bound is read from the registry each day (versions in `scorecard_accuracy_v2` × 104). After this declaration it is 11 × 104 = 1,144.

## Minimum live sample before any claim

These come from each bot's call coverage over 2018-02-18 to 2025-01-19. Only selections were computed; no outcome was looked at. "Windows with calls" is the share of non-overlapping *h* + 1-session blocks with at least one call. The binding constraint is independent windows (v2: 40 at 5-20 sessions, 30 at 40). The arithmetic is windows needed ÷ share × (*h* + 1), at about 229 sessions a year.

| Bot | Sessions with calls | Calls per session | Horizon | Windows with calls | Minimum live sessions | About |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `bot_ranker_spec` | 100% | 10.0 | 5 / 10 / 20 | 100% | 240 / 440 / 840 | **1.0** / 1.9 / 3.7 years |
| `bot_momentum` | 58% | 5.8 | 5 / 10 / 20 | 61% / 63% / 65% | 394 / 698 / 1,302 | 1.7 / 3.0 / 5.7 years |
| `bot_avoid_e2e4` | 99% | 10.3 | 20 | 100% | 840 | 3.7 years |
| `bot_market_timer` | 17% | 1.7 | 5 / 10 / 20 | 25% / 30% / 38% | 968 / 1,462 | 4.2 / 6.4 years |
| | | | 20 | 38% | 2,199 | 9.6 years |
| `bot_combined` | 17% | 1.7 | 5 / 10 / 20 | 25% / 30% / 38% | 968 / 1,462 / 2,199 | 4.2 / 6.4 / 9.6 years |
| `bot_new_listing` | 49% | 1.9 | 40 | 70% | 1,757 | 7.7 years |

These are lower bounds. **No bot can support a claim within a year.** The ranker at 5 sessions is the earliest, at about one year, and only if it is active in every window as it was historically. Selective bots pay for their selectivity in time.

## Look-ahead audit

`lookahead_audit` on real data (2018-02-18 to 2025-01-19; 8 random dates; the full panel compared with a panel truncated at each date): **all six bots pass** with 0 mismatches. Unit tests repeat the audit on synthetic data (`tests/test_league.py`).

## Dry run

`python -m src.league.run --date 2025-01-16 --dry-run` (a development date, so no holdout data was read):

- state: bull;
- `bot_avoid_e2e4`: 33 observations;
- `bot_momentum`: 10 calls (it includes SMHL and ANLB, which the avoid bot lists; this is the intended independence);
- `bot_ranker_spec`: 10;
- `bot_new_listing`, `bot_market_timer` and `bot_combined`: 0 (timer off).

Nothing was written. A live write would have been refused because the deadline had passed. A non-session date exits 3.

## Running it from cron (not scheduled)

The wrapper `scripts/cron/league_daily.sh` uses `flock` against overlapping runs. It writes the JSON report to `logs/league/run_*.json`, the Markdown leaderboard to `logs/league/`, and stderr plus the exit code to `logs/league/league.log`. Run it on the server's own Postgres after the day's prices are ingested and before 11:00 NPT the next day:

```
45 17 * * 0-4  ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/league_daily.sh
```

- Exit codes: 0 done; 2 too late (after the next session open, v2.1); 3 no price session for the date; 4 another run is active.
- Run `python -m src.database.holdout_guard` once after the first live run, so that the research role cannot read `league_leaderboard` or `league_runs` (both keyed by holdout dates).
- The NEPSE trading week has changed before (`docs/OPEN_ISSUES.md` P2). The wrapper runs Sunday to Thursday; days without a session exit 3 harmlessly. If the week changes, adjust the day list.
