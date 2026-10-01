# Event Research: Results

Protocol `event-prereg-v1` (`docs/EVENT_PREREG.md`, committed in eb9110f before any event return was computed). The evaluation code was committed in 7fd2b82 before its first run. Raw output: `docs/event_results.json` (`python -m src.backtest.event_eval`). Post-hoc diagnostics: `docs/event_diagnostics_post_hoc.json` (`python -m src.backtest.event_diagnostics`), written after the results were seen and **not** evidence.

Prices end at 2025-01-19 (last session loaded 2025-01-19, last exit 2025-01-19). Every trade passed `partition_rows`/`assert_development_only` with the 2025-01-20 cut, and the holdout was not read. No model was trained. `TestCounter` ran exactly the 8 planned tests. Family α = 0.00625; ledger α = 0.05/39 = 0.0013.

## Verdict

| ID | Hypothesis | Direction | Trades | Primary statistic | α = 0.00625 interval (conservative) | Gates | Result |
| --- | --- | --- | ---: | --- | --- | --- | --- |
| E1 | Book-close run-up | long | 709 | +0.74% net of 1% vs universe | low −0.48 | G2 G4 G5 G6 pass; **G1, G3 fail** | **Fail** |
| E2 | Post-book-close drift (bonus) | avoid | 683 | −3.68% gross vs universe, 20 sessions | high −2.62 | all 5 pass | **Pass (see caveats)** |
| E3 | New listing after initial run | long | **10** | −20.4% net | — | all fail | **Fail: untestable as registered** |
| E4 | End of ≥ 3 upper-circuit streak | avoid | 171 | −6.58% gross vs universe, 20 sessions | high −2.61 | all 5 pass | **Pass (see caveats)** |
| E5 | No-news volume spike, 20 sessions | long | 4,627 | −2.42% net vs universe | low −3.25 | only G6 passes | **Fail** (sign is opposite) |
| E6 | NEPSE trend + breadth in/out | timing | 2,235 sessions | Sharpe 0.93 vs 0.62 | bootstrap low −0.72 | G1-G4 pass; **G5 fails** | **Fail** |
| E7 | E5 at 60 sessions | long | 4,410 | −3.91% net | low −5.74 | only G6 passes | **Fail** |
| E8 | E5 at 120 sessions | long | 4,063 | −6.55% net | low −10.3 | only G6 passes | **Fail** |

Six of eight fail. E2 and E4 pass every registered gate, but both are **avoid** findings: they say when not to hold a stock, which a long-only investor can use only by selling. Both still need the caveats below and a fresh test on data after 2025-01-19 before anything is built on them.

Folds (4 equal session blocks): 2014-06-01 to 2017-02-02, 2017-02-05 to 2019-08-26, 2019-08-27 to 2022-06-09, 2022-06-10 to 2025-01-19.

## Detail

| | E1 | E2 | E3 | E4 | E5 | E7 | E8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Events / ineligible / filled | 852 / 136 / 709 | 852 / 129 / 683 | 234 / 224 / 10 | 219 / 40 / 171 | 4,776 / 6 / 4,627 | 4,776 / 6 / 4,410 | 4,776 / 6 / 4,063 |
| Not filled / corrupt price / exit past window | 1 / 6 / 0 | 8 / 1 / 31 | 0 / 0 / 0 | 2 / 3 / 3 | 14 / 91 / 33 | 14 / 219 / 122 | 14 / 352 / 336 |
| Exits delayed / stranded | 163 / 3 | 13 / 3 | 0 / 0 | 19 / 4 | 163 / 56 | 150 / 174 | 146 / 291 |
| Mean gross | +2.75% | +2.14% | −24.6% | −3.73% | +4.20% | +9.07% | +19.8% |
| Universe (registered, daily rebalanced) | +1.14% | +6.07% | −5.11% | +3.14% | +4.84% | +10.9% | +24.4% |
| Abnormal gross vs universe | +1.62% | −3.93% | −19.4% | −6.87% | −0.64% | −1.80% | −4.58% |
| Abnormal gross vs sector | +1.62% | −3.46% | −10.3% | −7.68% | −1.53% | −3.38% | −6.80% |
| 95% interval, primary, by date | −0.07 to +1.54 (net 1%) | −4.44 to −2.92 | | −8.89 to −4.27 | −2.93 to −1.92 (net) | −4.86 to −2.96 | −8.23 to −4.87 |
| Folds (primary) | −0.01 / +1.54 / +1.36 / −0.49 | −2.39 / −1.85 / −5.18 / −4.86 | − / − / −20.3 / −20.6 | −8.81 / −4.85 / −7.02 / −5.15 | −4.01 / −1.38 / −0.61 / −1.39 | −9.35 / −2.48 / −0.16 / −1.85 | −19.3 / −3.33 / −2.07 / −3.13 |
| Real-open entries only (primary) | +0.76% (518) | −4.69% (495) | −20.4% (10) | −6.38% (97) | −1.13% (3,506) | −1.31% (3,359) | −2.59% (3,099) |
| Gross − 1% − NEPSE | +1.83% | −1.77% | −15.7% | −4.77% | +0.93% | +3.07% | +8.49% |

**E6 (market rule):** in the market 38.9% of sessions, 86 switches, 2015-04-12 to 2025-01-19. Sharpe 0.93 at 1.0% switch cost and 0.74 at 1.5%, against 0.62 for buy-and-hold. CAGR 12.05% vs 11.46%. Maximum drawdown −28.1% vs −43.3%. Folds (rule vs buy-and-hold Sharpe): 1.12 vs 1.18, 1.18 vs −0.47, 1.08 vs 0.89, 0.68 vs 0.58. The block bootstrap of the Sharpe difference gives −0.72 to +1.29 at α = 0.00625 (positive in 78.8% of resamples), so G5 fails: the improvement is not distinguishable from luck over about 10 years and 86 switches. On the equal-weight universe (diagnostic only) the Sharpe is 1.87 vs 1.38 and the drawdown −27.6% vs −41.7%.

## What failed, plainly

- **E1 book-close run-up: fails.** Buying 15 sessions before a bonus book close beat the universe by 1.6% gross, but after a 1% cost the 99.4% interval includes zero, and it is positive in only 2 of 4 folds (negative in 2022-2025).
- **E3 new-listing lifecycle: untestable as registered.** The common eligibility rule (traded on ≥ 10 of the last 20 sessions) removes almost every new listing at the moment its first run ends, leaving 10 trades. This flaw in the pre-registration was not corrected; the ten trades lost 20% relative to the universe. The question remains open.
- **E5, E7, E8 no-news volume spikes: fail, and the sign is opposite.** Stocks with a 5× volume spike on an up day, with no corporate action nearby, trailed the universe after costs at every horizon (−2.4%, −3.9%, −6.6%). They beat the NEPSE Index only because the equal-weight universe did.
- **E6 market in/out rule: fails** the statistical gate, even though its point estimates beat buy-and-hold on Sharpe and drawdown in 3 of 4 folds.

## Caveats on the two passes

1. **The registered benchmark is biased upward.** Post-hoc, the daily-rebalanced equal-weight universe returned 3.39% per 20 sessions against 2.74% for a buy-and-hold equal-weight universe started on the same dates, a bias of +0.66% per 20 sessions and +3.0% per 60. This makes every abnormal return look more negative, which flatters avoid hypotheses and hurts long ones. Against the buy-and-hold universe (post-hoc), E2 is still −3.05% (99.4% interval −4.94 to −1.98, negative in all 4 folds) and E4 is still −5.70% (−9.51 to −1.78, negative in all 4 folds). E4's raw return is also negative: −3.7% on average, with 64% of trades losing money.
2. **E2 covers only surviving companies.** Corporate actions exist only for the 171 symbols active when they were scraped. Its windows also fall in strong markets (the buy-and-hold universe made 5.4% over the same sessions), so the result is "lags a rising market after a bonus book close", not "falls".
3. **The book-close announcement date is assumed, not observed.** This does not affect E2 or E4 (both use information available at the knowledge close), but it does affect E1.
4. **These are avoid rules.** They are useful as warnings or exit rules for existing holders and cannot be traded long-only. A 1% round-trip cost is small next to −3% to −6%, so selling and buying back would have paid in this sample.

## Limitations

- Before 2018-02-18, entry is at the next close, not a real open. Real-open-only results agree in sign for every hypothesis.
- Locked-upper sessions never occur in this data (on limit-up days the low equals the open), so the "no buy on locked days" rule barely binds. Exits did use the locked-lower rule (448 fully locked lower sessions in the data).
- Exit deferral stops after 20 sessions; stranded exits are valued at the last trade.
- Delisted stocks have no corporate actions recorded. Bonus drops above 12% are caught by the corrupt-return filter; smaller unrecorded drops are not.

## Exploratory (not evidence)

Written after seeing the results; none of this was tested under the protocol.

- A buy-and-hold equal-weight benchmark should replace the daily-rebalanced one in any future protocol. The toolkit's `benchmark_returns` stays as registered here; `event_diagnostics.buy_hold_universe` is the candidate.
- E3 could be re-specified without the liquidity rule (new listings trade every day once listed). Whether the first post-run 60 sessions are negative, as the ten trades suggest, needs a new protocol.
- The volume-spike results suggest spikes mark local tops in this market, the reverse of the registered idea. Reversing a failed direction after seeing it is exactly what the pre-registration forbids, so this stays an idea.
- E2 and E4, the two candidates, should be re-registered with the buy-and-hold benchmark and tested on data after 2025-01-19 once the floorsheet and price backfills cover it, before any product use.
