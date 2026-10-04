# Earnings-information hypotheses: results

Pre-registration: `docs/INFO_PREREG.md` (committed in `a4cfd1a` before anything below was computed). Code: `src/scorecard/info_eval.py`. Raw output: `docs/info_results.json`. Calls are in the append-only ledger as `replay` calls under the strategies `info_i1_…` to `info_i6_…`, version `v1`, graded as `info-v1`.

- **Window:** signal sessions 2014-06-01 to 2025-01-19 (2,435 sessions). Every exit is on or before 2025-01-19, and every read ran as the guarded research role.
- **Penalty:** *K* = 6 versions × 104 = 624.

**Nothing passed.** Five hypotheses are NO EVIDENCE and one is INSUFFICIENT SAMPLE. Every hypothesis with enough sample has a clearly **negative** edge against the same-date baseline. Trading on these announcements, as they could actually have been traded, did worse than the typical stock.

## Inputs

- 10,380 Sharesansar quarterly reports with a headline net profit, published by 2025-01-19. 8,472 also have a Merolagani date, and for 2,481 of them the Merolagani date was later, so the report's knowledge date was pushed later.
- 6,629 reports have a computable YoY growth: the prior year's same quarter was published earlier and |NP₋₁| ≥ Rs 1 million.
- 847 dividend declarations, the first announced on 2018-06-24.

## Results (each at its pre-registered horizon)

| | I1 YoY top quartile | I2 YoY top quartile | I3 turnaround | I4 dividend ≥ previous | I5 bonus → book close | I6 banks, YoY top quartile |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Horizon (sessions) | 20 | 40 | 20 | 10 | 10 | 20 |
| Matured calls / graded | 1,620 / 1,617 | 1,585 / 1,579 | 265 / 265 | 306 / 305 | 346 / 346 | 130 / 130 |
| Distinct dates / independent windows | 546 / 88 | 543 / 56 | 180 / 67 | 211 / 74 | 248 / 79 | 71 / 38 |
| Win rate / same-date baseline | 22.1% / 28.6% | 17.9% / 23.7% | 21.5% / 28.4% | 25.3% / 32.5% | 20.8% / 31.2% | 29.2% / 26.8% |
| **Edge** (points) | **−6.5** | **−5.8** | **−6.9** | **−7.2** | **−10.3** | +2.5 |
| Plain 90% lower bound / penalized | −9.6 / −15.2 | −9.0 / −12.8 | −10.1 / −19.7 | −9.2 / −17.5 | −13.0 / −20.5 | −10.0 / −20.7 |
| Expectancy at 0.5 / 1.0 / 1.5% | +0.36 / −0.03 / −0.42% | +2.60 / +2.22 / +1.83% | +0.72 / +0.36 / +0.01% | +0.03 / −0.43 / −0.89% | −0.88 / −1.35 / −1.81% | −0.41 / −0.88 / −1.34% |
| Excess over universe mean at 1% | −0.91% | −1.14% | −0.17% | −1.94% | −2.38% | −0.84% |
| Folds with positive edge (of 4) | 1 | 0 | 1 | 1 | 0 | 2 |
| Look-ahead audit | clean | clean | clean | clean | **fails** (see below) | clean |
| **Verdict** | NO EVIDENCE | NO EVIDENCE | NO EVIDENCE | NO EVIDENCE | NO EVIDENCE | INSUFFICIENT SAMPLE (130 < 150 calls, 38 < 40 windows) |

**Diagnostic controls** (not tests):
- **Every report with a computable YoY growth, at 20 sessions:** 6,620 graded; edge **−5.6 points**; excess −1.11%.
- **Every dividend declaration, at 10 sessions:** 832 graded; edge **−7.5 points**; excess −1.97%.

The selections do not beat these controls. I1 is −6.5 against −5.6, and I4 is −7.2 against −7.5. The growth, turnaround and dividend filters add nothing beyond "a report or declaration was published".

I2's expectancy is positive at every cost, but only because the market rose over 40-session windows. It is still −1.1% against the universe mean, and it loses on win rate.

## Events per year feeding each test (by signal year)

| Year | I1 / I2 | I3 | I4 | I5 | I6 | All reports (control) | All declarations (control) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2014 (from June) | 64 | 20 | – | – | 7 | 305 | – |
| 2015 | 144 | 39 | – | – | 15 | 630 | – |
| 2016 | 156 | 15 | – | – | 19 | 620 | – |
| 2017 | 130 | 15 | – | – | 7 | 568 | – |
| 2018 | 152 | 23 | 1 | 1 | 30 | 600 | 81 |
| 2019 | 125 | 16 | 42 | 3 | 13 | 435 | 114 |
| 2020 | 97 | 8 | 67 | 52 | – | 463 | 153 |
| 2021 | 216 | 24 | 84 | 119 | 2 | 717 | 157 |
| 2022 | 114 | 22 | 42 | 63 | 8 | 745 | 110 |
| 2023 | 153 | 30 | 32 | 65 | 16 | 724 | 118 |
| 2024 | 269 | 53 | 38 | 43 | 13 | 822 | 106 |
| 2025 (to 19 Jan) | – | – | 1 | – | – | – | 3 |

- **I4 and I5** rest on six years of declarations. Most of their events are in 2020-2021, so two years dominate the result.
- **I6** never had more than 30 bank events in a year, and none in 2020.
- **I3** averages about 25 turnarounds a year.

## Look-ahead audit failure (I5)

The audit re-selects events with every report and declaration published after *t* removed, and with the session calendar cut at *t*. I5's rule ("book close at least 12 sessions after *t*") counts trading sessions up to a future date. That uses the realized NEPSE calendar, holidays included, which cannot be known at *t*: closures are often declared at short notice. With the calendar cut at *t*, the rule cannot be evaluated, so the truncated selection differs from the full one on every I5 date. The random 40-date audit caught 3 of them, and a full pass showed every I5 date affected.

This is a real defect in the pre-registered rule, not a bug in the audit. Under protocol v2 a failed audit blocks a PASS. I5 is reported as NO EVIDENCE because its edge is −10.3 points, so the defect does not change its verdict. It was not repaired after the fact. A future version would have to count weekdays, or use the announced trading calendar as known at *t*, and register again.

## Exploratory (post hoc, not tests)

These observations were made after the results and carry no evidential weight.

- **A fifth of report-based calls cannot be filled.** For I1, 363 of 1,620 calls (22%) were unfilled at the next open, and another 62 were blocked or stranded, all graded wrong. For I3 the unfilled share is 29% (78 of 265). For the declaration tests it is 7-8% (I4 25, I5 25). Liquidity failures (unfilled, blocked or stranded) make up 34% (I1) and 43% (I3) of the wrong calls. A likely reading, not tested: stocks with good reports are often locked at the upper circuit, or not trading, at the first open after the news is public. The information is priced before a public-data trader can act.
- **I6 (banks)** is the only test with a positive edge (+2.5 points, 2 of 4 folds). With 130 calls, 38 windows and a plain lower bound of −10.0 points, it is indistinguishable from zero.

## Conclusion

Dated quarterly-report and dividend information, used as it became public and with realistic next-open fills, did not produce edge on 2014-2025 for any pre-registered rule. Every adequately sampled rule was worse than the same-date baseline, and no better than simply trading every report or declaration. No information-based candidate goes forward to live tracking from this test.
