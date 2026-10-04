# Ranker r1 and meta-labeler m1: results

Run on 2026-10-04 after both pre-registrations were committed and registered (`docs/RANKER_PREREG.md` at 11:57:43 NPT, `docs/META_PREREG.md` at 11:57:50 NPT). Window 2014-06-01 to 2025-01-19, read through the research role; the holdout was not touched. Protocol `accuracy-v2.1`; *K* = 40 strategy versions in `scorecard_accuracy_v2` × 104 = **4,160** at evaluation time. Raw outputs: `docs/ranker_results.json`, `docs/meta_results.json`, `docs/ranker_ic_diagnostic.json`. Code: `src/ranker/`.

## Bottom line

**Nothing passes.** No ranker model has an edge above +0.8 points, so all four are NO EVIDENCE. The meta-labeler roughly doubles the edge at every horizon and reaches **+8.6 points at 40 sessions**, but its lower bounds are not above zero once the penalty is applied, its stated probabilities fail calibration, and it inherits the ranker's failed look-ahead audit. It is also NO EVIDENCE. Nothing here is registered as a challenger bot (see the last section).

## The look-ahead audit failed (r1 is "leaky")

The pre-registered audit recomputed every feature at 20 random test dates from inputs truncated at that date. Across 12 values on 4 dates it found differences, all in the **earnings features** (`since_report` 4, `report_count` 4, `profit_positive` 3, `profit_growth_yoy` 1).

The cause:
- The earnings timing was copied from the earlier earnings study (`src/scorecard/info_eval.py`). It dates a report by the **later** of its Sharesansar and Merolagani publication dates. Example: HDHPC on 2022-07-19, where Sharesansar had published but Merolagani had not yet.
- With the full history, the report counts as "not yet known", because the later Merolagani date lies in the future. Truncated at the date, it counts as known from Sharesansar's date.
- It is a conservative delay rather than a peek at returns. But whether a feature updates at *t* depends on an event after *t*, and the pre-registered rule says any mismatch makes the model leaky, so it cannot pass.

**The same convention affects the earlier earnings study (`docs/INFO_RESULTS.md`, I1-I6).** Its event dates also depend on when Merolagani published later. Its verdicts were all "no pass", so no claim rests on it, but its event timing should be described as "known once both portals had published".

The audit itself was shown to work. Before the run, 0 mismatches on two clean dates (56 and more eligible stocks each), and a planted future-looking `ret_5` was caught with 206 mismatches.

## Ranker r1: four models, top 10 eligible calls per session, 2018-02-18 to 2025-01-19

| Model | Calls (graded) | Win rate | Same-date baseline | **Edge** | Lower 90% | Penalized lower | Expectancy at 1% | Excess at 1% | Folds > 0 | Windows | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| h5 at 5 sessions | 15,842 | 30.19% | 30.02% | **+0.17** | −0.55 | −2.15 | −0.26% | −0.91% | 1 of 4 | 265 | NO EVIDENCE |
| h10 at 10 | 15,792 | 30.81% | 30.55% | **+0.27** | −0.78 | −3.02 | +0.32% | −0.97% | 3 of 4 | 144 | NO EVIDENCE |
| h20 at 20 | 15,681 | 31.56% | 31.73% | **−0.17** | −1.62 | −4.69 | +1.31% | −1.30% | 2 of 4 | 75 | NO EVIDENCE |
| h40 at 40 | 15,452 | 25.89% | 25.14% | **+0.75** | −1.25 | −5.51 | +4.08% | −1.10% | 3 of 4 | 38 | NO EVIDENCE |

Edges and bounds are in points. Excess expectancy is measured over the same-date universe mean. Positive raw expectancy at 20 and 40 sessions only reflects a rising market; excess over the universe is negative at every horizon.

Other details:
- **Purge and embargo held in every fold**: the last training exit was always more than 5 sessions before the fold's first session.
- The grid selection was usually `num_leaves 15`. The selected points are listed per fold in the JSON.
- Most failures are market-wide or sector-wide (h5: market 44%, sector 25%, model 29%); liquidity is about 1-2%.
- **Most important features (gain):**
  - at 5 and 10 sessions: sector 20-session return, 20-session turnover, NEPSE 20- and 60-session returns, market turnover ratio, and price level;
  - at 20 and 40 sessions: price level, profit sign, sessions since rights, sessions since the last dividend declaration, and earnings growth.

  The earnings features that failed the audit are among the top features at the longer horizons.

### Why a good ranking did not become an edge

The out-of-sample daily Spearman correlation between prediction and target is high:
- 0.10 to 0.15 in every fold at 5 sessions;
- 0.03 to 0.23 at 20 sessions;
- still **0.07 to 0.09 within the eligible (liquid) set**.

The ranking is real. But the diagnostic (`docs/ranker_ic_diagnostic.json`) shows where it comes from:

| Model | Top 10 mean excess (filled) | Rest of eligible set | Top 10 unfilled |
| --- | ---: | ---: | ---: |
| h5 | +0.22% | −0.26% | 1.1% |
| h10 | +0.18% | −0.45% | 1.7% |
| h20 | −0.22% | −0.80% | 2.6% |
| h40 | +0.29% | −1.46% | 4.3% |

The model separates stocks that will **lag** from the rest. Its top picks are only about average. After the 1% cost, and under the scorecard's definition of a correct call (net > 0 and above the same-date median), the top 10 win about as often as a random stock. This suggests testing the ranker's **bottom** decile as an avoid signal. That would be a new hypothesis needing its own pre-registration and *K* charge; it is not a result.

## Meta-labeler m1: logistic filter on r1's calls, keeping roughly the top 30%, 2019 to 2025-01-19

| Model | Kept calls | Win rate | Baseline | **Edge** | Lower 90% | Penalized lower | Excess at 1% | Folds > 0 | Windows | Brier vs baseline-forecast Brier | ECE | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| h5 | 4,332 | 35.00% | 31.28% | **+3.72** | +2.51 | −1.64 | −0.66% | 3 of 4 | 191 | 0.225 vs 0.189 (worse) | 0.010 | NO EVIDENCE |
| h10 | 3,675 | 39.65% | 33.85% | **+5.80** | +2.98 | −4.08 | −0.30% | 3 of 4 | 102 | 0.239 vs 0.203 (worse) | 0.018 | NO EVIDENCE |
| h20 | 5,017 | 38.19% | 35.14% | **+3.05** | +2.27 | −3.60 | −0.72% | 2 of 4 | 52 | 0.239 vs 0.203 (worse) | 0.066 | NO EVIDENCE |
| h40 | 2,891 | 38.12% | 29.53% | **+8.59** | −0.18 | −9.85 | +2.58% | 4 of 4 | 30 | 0.242 vs 0.224 (worse) | 0.057 | NO EVIDENCE |

**What failed:**
- every penalized lower bound;
- calibration at every horizon: the stated probabilities score worse than simply quoting the same-date baseline;
- the inherited leak;
- excess expectancy at 5, 10 and 20 sessions;
- at 40 sessions, the plain lower bound, at only 30 independent windows, the minimum.

**The selection is concentrated in one year.** Kept calls by year at 10 sessions: 2019 228, 2020 269, **2021 1,982**, 2022 119, 2023 109, 2024 968. The filter mostly learned "the 2021 boom", a regime effect with very few independent episodes. The +8.6-point edge at 40 sessions rests on 30 windows. It is exactly the kind of result the penalty is there to discount.

## Is anything ready to run live?

No:
- r1 uses the floorsheet broker features, whose store ends on 2025-01-19. No live pipeline computes them from daily floorsheets.
- Its earnings timing fails the audit.
- m1 depends on r1.

Under the pre-registered rule (register a challenger only if it is ready to run live), **no challenger bot was registered**. The seven fold models per horizon are saved outside the repository in `arthasignal-ai/derived/ranker_r1/` for reference only.

## What would come next (not run; each needs a new pre-registration and raises *K*)

- **r2:** the same design with earnings dated by the first portal to publish, and either a live broker-feature pipeline or no broker features.
- **Ranker as an avoid signal:** the bottom 10% of eligible stocks by r1's score, as avoid observations.
- **A meta-labeler with a calibration step and a regime-diversity requirement**, for example capping the share of calls kept in any one year.
