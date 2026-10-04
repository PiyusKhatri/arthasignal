# Simulation protocol v1

- **Version:** `sim-protocol-v1`, locked 2026-10-04.
- **Constants:** every number and rule below lives in `config/simulation_protocol.yaml`. Code reads rules only from that file (`src/simulation/protocol.py`), and the SHA-256 of the file bytes is the protocol's identity. If the doc and the config disagree, the config is what runs, and the disagreement is a bug to fix in a new version.
- **Scope:** this phase locks the rules and implements only call grading and costs (`src/simulation/grading.py`, `src/simulation/costs.py`), tested on synthetic price paths. Nothing was built yet: no simulator, no score, and no study run on real data. The only real-data reads were a count of trading sessions per month from `trading_calendar` (section 3) and a check of the trial table, both on the research role.
- **Holdout:** the holdout (2025-09-30 onward) is never read by this protocol. Every development rule caps prices at 2025-09-29.

**Earlier studies have already seen the exam years.** The event study, broker-flow study, information study, ranker, model v0 matrix and the price-integrity work all used prices through 2025-01-19. So 2021-2024 are **not fully unseen**: the owner and the code have seen what happened in those years. Exam-year results are therefore evidence of consistency, not proof. **The final verdict comes only from the untouched holdout (2025-09-30 onward) and from live calls.**

## 1. Calls

| Call | Meaning |
|---|---|
| **BUY** | Buy at the next session open. |
| **HOLD** | A holder should keep the stock. |
| **WAIT** | Wait and watch. Not graded for accuracy. |
| **NO_BUY** | Do not buy now. |
| **SELL** | Exit, or stay out. |

NEPSE has no short selling, so SELL never means "go short".

## 2. Confidence score (built in a later phase)

- **Range:** −100 to +100, built from these pillars:
  - technical;
  - volume;
  - floorsheet and broker flow;
  - fundamentals;
  - corporate events (AGM, bonus, dividend, book close);
  - news;
  - market state;
  - historical sentiment proxies (IPO oversubscription, new demat accounts, margin lending, turnover and breadth).
- **Initial bands:**

  | Score | Call |
  |---|---|
  | ≥ +80 | BUY |
  | +50 to +79 | HOLD |
  | −49 to +49 | WAIT |
  | −79 to −50 | NO_BUY |
  | ≤ −80 | SELL |

- **Tuning:** thresholds and pillar weights may be tuned **only on learning years**, and are frozen into a version before any check or exam run.
- **Missing data:** a pillar with no point-in-time data on a date contributes **0**, and the call records which pillars were missing.

## 3. Horizons

- **Conversion:** 1 month = **19 sessions**. That is the median number of trading sessions per calendar month in `trading_calendar` from 2014-07 to 2019-12 (66 months; mean 19.36, range 6-23; the low months are holiday and shutdown months).
- **Only the counts are fixed.** Session counts always step over real trading sessions of the calendar, never weekdays, so holidays and the 2020 shutdown lengthen the calendar span, not the session count.

| Class | Months | Sessions (min to max holding) |
|---|---|---|
| short | 1-3 | 19 to 57 |
| mid | 3-7 | 57 to 133 |
| long | 12-15 | 228 to 285 |

- **Each call carries** its horizon class and a maximum holding period in sessions inside the class range. The entry session is holding session 1.
- **Not covered:** 7 to 12 months is covered by no class (open question Q3).

## 4. Entry

- **Real opens:** entry is at the **open of the first session after the call date** (from 2018-02-18).
- **Before 2018-02-18:**
  - **The problem:** the stored open equals the previous close on 98.7-99.6% of rows every month (`docs/PHASE_LOG.md`, data survey), so it is not a price anyone could trade at.
  - **The rule:** every bar dated before 2018-02-18 is **reduced to its close** (open, high and low are replaced by the close). Entry is therefore at the **close of the next session**.
  - **Why this rule:**
    - It is the first real traded price strictly after the call.
    - It uses nothing from the call date beyond the close the call was made on.
    - It matches the rule already used in the event, broker-flow and information studies.
    - Reducing high and low too means targets and stops before 2018-02-18 are checked on closes only. That is slightly pessimistic for targets and optimistic for stops, but it never uses an intraday price whose reliability has not been established (open question Q8).
- **No fill:** there is no fill when the entry session has **no trade**, or is **locked at the upper circuit**. Locked means high = low and the close is within 0.5 point of the limit move from the previous close (10% limit, 15% from 2026-04-20, as in `src/backtest/event_study.py`).
- **No backfill:** an unfilled call is recorded as `unfilled` and never moved to a later session.
- **Settlement:** a bought stock cannot be sold before it settles. The first session at which a sell is allowed is entry session + **3**. This is conservative: the settlement cycle moved from T+3 to T+2 at some point, and the date was not confirmed (Q5).

## 5. Exit (BUY, and the counterfactual buys used for NO_BUY, WAIT and risk control)

- **Exit at whichever comes first:** target, stop-loss or horizon end. Checks start at the first session a sell is allowed.
- **Gaps:** an open at or through the stop or target fills **at the open**. Otherwise a low at or below the stop fills at the stop, and a high at or above the target fills at the target.
- **Both touched in one session:** the **stop is assumed first**, which is conservative.
- **Horizon end:** the close of the last holding session.
- **No trade, or locked at the lower circuit, when an exit is due:** the exit moves to the open of the next session that trades and is not locked down. The delay is recorded. If the path ends first, the call stays `pending` and is not graded.
- **Target and stop:**
  - chosen by the system per stock and situation, by a rule fitted on learning years only and frozen with the version;
  - each call's target and stop are written and locked when the call is made;
  - this phase does not define the rule (Q1).

## 6. Costs (`src/simulation/costs.py`)

- **Trade size:** a fixed notional of **NPR 100,000** per call, in whole shares (floor of notional / entry price, at least 1). Commission tiers depend on the traded amount, so a size must be assumed (Q4).
- **Per side:** broker commission at the tier rate for the whole amount (minimum NPR 10), plus the SEBON fee, plus the DP charge.
- **Capital gains tax:** the CGT rate times the positive net gain after both sides' costs. Short-term or long-term is set by calendar days held (> 365 is long).

| Item | Schedule used | Status and source |
|---|---|---|
| Commission before 2016-08-01 | flat 1.00% | **Not confirmed.** Sources give only "0.7-1 percent" before the July 2016 cut ([Kathmandu Post 2019](https://kathmandupost.com/money/2019/10/05/securities-board-of-nepal-mulls-lowering-stockbrokers-commissions)). Conservative top of the range; the exact July 2016 date was not found, so the old rate is kept to 2016-07-31 |
| Commission 2016-08-01 to 2020-12-26 | 0.60 / 0.55 / 0.50 / 0.45 / 0.40% for ≤ 50k / ≤ 5 lakh / ≤ 20 lakh / ≤ 1 crore / above | **Partly confirmed.** The 0.60-0.40 range is confirmed ([Himalayan Times 2020-12-27](https://thehimalayantimes.com/business/brokers-commission-slashed-by-33-per-cent-from-today)); the Kathmandu Post gives three tiers (0.6/0.5/0.4). The higher reading per tier is used |
| Commission 2020-12-27 to 2024-05-13 | 0.40 / 0.37 / 0.34 / 0.30 / 0.27% | Confirmed ([Himalayan Times](https://thehimalayantimes.com/business/brokers-commission-slashed-by-33-per-cent-from-today), [myRepublica](https://myrepublica.nagariknetwork.com/news/commission-for-brokerage-service-reduced-by-up-to-60-percent/)) |
| Commission from 2024-05-14 | 0.36 / 0.33 / 0.31 / 0.27 / 0.24% | Confirmed ([Sharesansar](https://www.sharesansar.com/newsdetail/sebon-to-enact-revised-commission-rates-for-stockbrokers-implementation-begins-today-2024-05-14), [Investopaper](https://www.investopaper.com/news/stock-broker-commission-nepal/)) |
| Minimum commission | NPR 10 per side | Current figure, secondary source ([Kharchapatra](https://kharchapatra.com/blog/nepse-share-trading-costs-nepal)); applied throughout |
| SEBON fee | 0.015% per side | Confirmed for the current schedule. A 10% cut was announced in October 2023 ([Merolagani](https://eng.merolagani.com/NewsDetail.aspx?newsID=96273)) with no published new rate, and the rate before 2020 was not found. **0.015% is kept throughout (conservative)** |
| DP charge | NPR 25 per scrip per side | Rs 25 per company per settlement is confirmed for today. Sources disagree on whether the buy side pays it, and older amounts were not found. **Charged on both sides throughout (conservative)** |
| CGT to 2021-07-15 | 5% | Section 95A, Income Tax Act, as amended by Finance Act 2068 ([KBC](https://www.kbc-ca.com.np/_uploads/capital-gain-tax.pdf)); "previously only 5%" ([NBSM budget note 2078/79](https://www.nbsm.com.np/uploads/large/1622709726242738.pdf)) |
| CGT 2021-07-16 to 2026-07-16 | 7.5% held ≤ 365 days, 5% longer | Confirmed (NBSM budget note 2078/79) |
| CGT 2026-07-17 to 2026-09-21 | 10% / 7.5% | Secondary sources only; not confirmed |
| CGT from 2026-09-22 | 5% / 3.75% | Gazette notice reported by [Sharesansar](https://www.sharesansar.com/newsdetail/capital-gains-tax-rates-reduced-decision-published-in-gazette-2026-09-22); [Nepal Press](https://nepalpress.com/2026/09/16/764315/relief-for-individual-investors-in-the-stock-market-capital-gains-tax-reduced-to-3-5-and-5-percent/) gives 3.5% long-term. The higher figure is used |

- **Examples at NPR 100,000:** a round trip costs about **1.06% plus CGT** before 2016-08 and about **0.71% plus CGT** from 2024-05.
- **Rates that only affect live calls:** the two 2026 CGT rows only touch live calls; they are recorded so the cost model stays one schedule.

## 7. Grading (`src/simulation/grading.py`)

- **The headline metric is call accuracy** = right calls / graded calls.
- **Statuses:** every call gets one of `graded`, `unfilled`, `pending` (path ends before resolution) or `ungraded` (WAIT, and unfilled NO_BUY).

| Call | Right when | Wrong when |
|---|---|---|
| **BUY** | Net profit after commission, SEBON fee, DP charges and CGT is **> 0**. Exiting above entry without reaching the target is right | Net ≤ 0 (a 1-rupee net loss is wrong); any **stop-loss exit** is wrong even if it happens to be above entry; **unfilled** is wrong (counted in accuracy, as in the earlier accuracy protocols; Q2) |
| **SELL** | Price at exit (target, stop or horizon close) is **below the close on the call date** | At or above it. No costs (no position is opened) |
| **NO_BUY** | A buy at the next open held to horizon end (no target, no stop) would have **lost money** after all costs and tax (net < 0) | Net ≥ 0. If that buy could not fill, NO_BUY is `ungraded` and counted (Q2) |
| **HOLD** | A **close at or above the reference** (close on the call date) within the horizon, before any stop | Stop touched first (low at or below the stop; same-session ties go to the stop), or the horizon ends below the reference |
| **WAIT** | Not graded | - |

- **Missed opportunities on WAIT:**
  - **missed BUY:** a buy at the next open held to horizon end would have been right (net > 0);
  - **missed SELL:** the close at horizon end is below the reference.

  Both are reported per period.

## 8. Risk control score (separate from accuracy)

- **Calls covered:** filled BUY calls with a resolved exit. Each is paired with its **hold-to-horizon counterfactual**: the same entry, no target, no stop, exit at horizon end under the same delay rules.
- **Components:**
  - **stop_exits:** count of stop exits.
  - **loss_avoided_npr:** sum over stop exits of (actual net − counterfactual net). Positive means the stops saved money.
  - **stop_regret_rate:** share of stop exits whose counterfactual net was > 0, i.e. the stop turned a right call into a wrong one.
  - **tail_mean_actual / tail_mean_hold:** mean net return of the worst 10% of calls (at least 1 call), with exits and held to horizon.
  - **max_drawdown_actual / max_drawdown_hold:** the largest peak-to-trough fall of the running sum of per-call net returns (each call one unit, starting at 0). The actual series is ordered by exit date and the counterfactual by its own exit date.
  - **mae_mean / mae_p95:** maximum adverse excursion, the lowest low from entry to exit divided by entry minus 1, as its mean and 95th percentile magnitude.
- **Risk control score** = 100 × (tail_mean_actual − tail_mean_hold) / |tail_mean_hold|, clipped to −100…+100. It is undefined when tail_mean_hold ≥ 0, which means there is no tail loss to control.
  - **+100:** the exit rules removed the whole worst-decile loss.
  - **0:** they changed nothing.
  - **Negative:** they made the tail worse.

## 9. Targets and reporting

- **Pass, per period:** a period passes only when **all** of these hold:
  1. **Accuracy ≥ 60%** over all graded calls in the period, however many there are;
  2. **above a same-date random baseline**: the one-sided 95% lower bound of (accuracy − baseline) is > 0;
  3. **minimum sample**: ≥ **100 graded calls** on ≥ **30 distinct call weeks**.
- **The baseline:**
  - For each call, the baseline is the share of eligible stocks on the same call date that would have been right under the same call type, horizon class, holding period and frozen target/stop rule. That is the hit rate of a random pick that day.
  - The interval is clustered by call week (all calls made at one weekly step share market shocks).
- **Why 100 calls and 30 weeks:**
  - At 60% accuracy, 100 independent calls give a binomial standard error of 4.9 points, so a 95% interval of about ±10 points. Fewer cannot separate 60% from a coin.
  - Cluster-robust intervals are unreliable with fewer than about 30-50 clusters (Cameron & Miller 2015). A year has about 48-50 weekly steps, so 30 weeks means the system called in at least 60% of the weeks.
  - **Long horizons are an exception:** a long call's 228-285-session path covers more than a year, so one exam year holds less than one independent long window. Long-horizon results per year are descriptive only (Q6).
- **Always reported:**
  - accuracy, baseline and edge per **call type**, per **horizon class** and per **score band**; breakdown cells with < 30 graded calls are marked insufficient;
  - **calibration**: accuracy per score bucket (80-89, 90-100, 50-64, 65-79, and the mirrored negative buckets), with the check "do +85 calls succeed more often than +55 calls";
  - **coverage**: graded calls / eligible stock-weeks;
  - unfilled, pending and censored counts.

## 10. Periods

| Period | Dates | Runs |
|---|---|---|
| Learning | 2014-06-01 to 2019-12-31 | Unlimited |
| Check | 2020 | One run per frozen version |
| Exam | 2021, 2022, 2023, 2024, strictly in that order | Exactly one run per frozen version per year |
| Pre-holdout (proposed) | 2025-01-01 to 2025-09-29 as `exam_2025a` | One run per frozen version, after 2024 |
| Holdout | 2025-09-30 onward | Final verdict only |

- **No back-testing on the same year:** a lesson learned from an exam year can only be judged on a **later** year. A version created after seeing exam 2022 may be run on 2023 and later, never on 2022 or earlier exam years.
- **2025-01 to 2025-09-29:**
  - **Proposal:** treat it as the last exam (`exam_2025a`). It is the least-seen development window: no earlier study used prices after 2025-01-19.
  - **The catch:** any call whose path would reach 2025-09-30 is **censored** (not graded, counted). Outcomes may use prices after a period's end, but never past 2025-09-29.
  - **What that costs:** this censors most long-horizon calls made after about 2024-08 and nearly all mid and long calls in 2025 (Q7).

## 11. Weekly time machine and point-in-time rules

- **Each step is one trading week.** NEPSE traded Sunday-Thursday before 2026-04-08 and Monday-Friday after (`config/nepse_calendar.json`).
- **Step order:**
  1. **Reveal** all data whose knowledge time is ≤ the last close of week *W*.
  2. **Score and call;** write each call with its type, score, pillar contributions, missing pillars, horizon, holding period, target and stop, and lock it (stored with its hash).
  3. **Advance:** reveal week *W*+1 session by session. Entries fill at the first session's open; open calls are checked against targets, stops and horizons in session order.
  4. At the last close of *W*+1, repeat.
- **Point-in-time per pillar.** Knowledge time is the **first moment the value was actually observable**:

| Pillar | Knowledge time |
|---|---|
| Technical, volume, turnover, breadth, market state | Session close (15:00) of the session they describe |
| Floorsheet and broker | After that session's close |
| Fundamentals | Publication time of the report (`quarterly_report_announcements.published_date`, first capture), never the fiscal period end |
| Corporate events (AGM, bonus, dividend, book close) | Announcement time (`dividend_declarations.announcement_date`, first capture in `corporate_action_sources`), never the book-close or ex date |
| News | `text_items.first_seen_at`; historical news only with a verified publication timestamp |
| IPO oversubscription | Publication of the allotment result |
| New demat accounts | CDSC publication date of the monthly figure |
| Margin lending | NRB publication date of the monthly statistic |

- **The earlier-of-sources rule must not peek.**
  - **When there is a timestamp:** a value's knowledge time is the earliest time **any source actually had it**, proven by a capture or publication timestamp from that source.
  - **When there is none:** a source date with no timestamp, or a page that may have been back-dated, counts from its first capture, or from its stated date plus one session when it was never captured.
  - **Revisions:** revised values are used only from their own revision time (vintage data), never back-filled into earlier weeks.

## 12. Learning loop

- **After each run**, a post-mortem tags every wrong call with one or more causes:
  - **market-wide move:** NEPSE index return over the call window against the call;
  - **sector move:** sector equal-weight minus index;
  - **news or policy:** a news or policy item in the window;
  - **circuit lock:** the call's path hit a locked session;
  - **liquidity:** turnover below the eligibility floor or no-trade sessions;
  - **corporate action;**
  - **model error:** none of the above explains at least half of the shortfall.

  It then lists recurring patterns. The magnitude decomposition of `docs/ACCURACY_PROTOCOL.md` v2 is reused so that "any event in the window" does not absorb everything.
- **Change limits:** a new version may change **at most 3 things**, each with a written reason tied to the post-mortem.
- **Storage:** every run and every version is stored **immutably** with its config hash and code commit, and is never deleted. This protocol version is registered in `backtest_variant_trials` (family `simulation_protocol`).

## 13. Multiple testing

- **What the repository does now** is a global penalty counter. It is Bonferroni with *K* = versions registered in the family × 104 cells and level α/*K* (`docs/ACCURACY_PROTOCOL.md`; *K* = 41 × 104 for `scorecard_accuracy_v2` today). It is simple and safe, but:
  1. it ignores the strong correlation between versions that differ in one threshold, so its power falls with every harmless variant;
  2. it charges learning-year tuning, which is never a claim, at the same price as exam runs;
  3. it grows without bound, so after enough honest iterations nothing can ever pass.

| Method | What it does | Fit to this design |
|---|---|---|
| White's Reality Check (White 2000) | Bootstrap test that the best of *K* rules beats a benchmark | Single-step; loses power when poor versions are in the set; says nothing about which versions pass |
| Hansen SPA (Hansen 2005) | Studentized Reality Check that re-centres clearly poor rules | More power than RC, but still a single global null |
| **Romano-Wolf stepdown** (Romano & Wolf 2005; stepwise SPA, Hsu, Hsu & Kuan 2010) | Bootstrap of the max statistic, stepping down to identify **which** versions beat the baseline, with family-wise error control and the dependence between versions built in | **Best fit**: claims are per version and per year, versions are highly correlated, and the test runs only on the small set of frozen versions actually run on a check or exam year |
| Deflated Sharpe ratio (Bailey & López de Prado 2014) | Deflates a Sharpe ratio for the number of trials and non-normality | Built for Sharpe, not accuracy; useful only as an "expected best of *N* trials" sanity check on learning years |
| PBO via CSCV (Bailey, Borwein, López de Prado & Zhu 2017) | Probability that the in-sample best configuration ranks below the median out of sample, across combinatorial splits | **Good learning-year diagnostic**: learning runs are unlimited, and PBO measures whether the tuning is fitting noise |
| Benjamini-Hochberg FDR (1995); haircuts (Harvey, Liu & Zhu 2016) | Controls false discoveries rather than any false claim | Too lenient for a headline claim to users |

- **Recommendation (owner to approve, Q9):**
  1. **Learning years:** no multiplicity correction, since nothing there is a claim. Report PBO (CSCV, 16 time blocks) over all versions tried. A version whose PBO > 0.5 should not be frozen.
  2. **Check and each exam year:** a Romano-Wolf stepdown over the frozen versions run on that year.
     - **Statistic:** studentized (accuracy − baseline).
     - **Resampling:** stationary block bootstrap over call weeks (Politis & Romano 1994), mean block length = the horizon in weeks, 10,000 resamples, family α = 0.05.
     - **Claims:** only the headline per version is a claim. Breakdowns are descriptive.
     - **Why this is fair:** a version runs once per exam year, so the family is small and honest.
  3. **Holdout:** a single pre-declared version and a single test, so no correction is needed. If more than one version is sent, use Romano-Wolf over them.
  4. **The legacy counter:** keep computing and printing the global counter next to the new result for continuity. It is not the gate for simulation claims. Existing studies are not changed.

**References:**
- White, H. (2000). A Reality Check for Data Snooping. *Econometrica* 68(5), 1097-1126.
- Hansen, P. R. (2005). A Test for Superior Predictive Ability. *Journal of Business & Economic Statistics* 23(4), 365-380.
- Romano, J. P. & Wolf, M. (2005). Stepwise Multiple Testing as Formalized Data Snooping. *Econometrica* 73(4), 1237-1282.
- Hsu, P.-H., Hsu, Y.-C. & Kuan, C.-M. (2010). Testing the predictive ability of technical analysis using a new stepwise test without data snooping bias. *Journal of Empirical Finance* 17(3), 471-484.
- Bailey, D. H. & López de Prado, M. (2014). The Deflated Sharpe Ratio. *Journal of Portfolio Management* 40(5), 94-107.
- Bailey, D. H., Borwein, J., López de Prado, M. & Zhu, Q. J. (2017). The Probability of Backtest Overfitting. *Journal of Computational Finance* 20(4), 39-69.
- Politis, D. N. & Romano, J. P. (1994). The Stationary Bootstrap. *JASA* 89(428), 1303-1313.
- Benjamini, Y. & Hochberg, Y. (1995). Controlling the False Discovery Rate. *JRSS B* 57(1), 289-300.
- Harvey, C. R., Liu, Y. & Zhu, H. (2016). ... and the Cross-Section of Expected Returns. *Review of Financial Studies* 29(1), 5-68.
- Cameron, A. C. & Miller, D. L. (2015). A Practitioner's Guide to Cluster-Robust Inference. *Journal of Human Resources* 50(2), 317-372.

## 14. Open questions for the owner

Each item below has a default in the config so the code is concrete. **None is decided**; each needs the owner's answer before the simulator is built.

1. **Target and stop rule.** What family should the frozen per-stock rule come from? Options: ATR multiples, recent swing levels, volatility percentiles. Must every BUY and HOLD carry a stop?
2. **Unfilled calls.**
   - **BUY:** an unfilled BUY (no trade, or locked upper circuit) is counted **wrong** by default, as in earlier protocols. Should it instead be excluded and reported?
   - **NO_BUY:** an unfilled NO_BUY is ungraded by default.
3. **The gap between mid and long.** No class covers 7 to 12 months. Should one be added, or the long class widened to 7-15 months?
4. **Notional trade size.** NPR 100,000 per call is the default. It sets the commission tier and the weight of the fixed Rs 25 DP charge.
5. **Settlement lag.** The earliest sell is entry + 3 sessions throughout (T+3, conservative). Should T+2 apply from its official start date once that is confirmed?
6. **Long horizons within one exam year.** Should long-horizon accuracy be judged only on pooled exam years? One year holds less than one independent long window.
7. **2025-01 to 2025-09-29.**
   - Should it be the last exam (`exam_2025a`) as proposed?
   - Should calls whose path reaches the holdout be censored? That is the default, and it removes most 2025 mid and long calls.
   - Separately: outcomes of 2024 long calls use 2025 prices, which the post-mortem would then see before `exam_2025a`. Should those grades be sealed until `exam_2025a` has run?
8. **Pre-2018 intraday prices.** The default reduces every bar before 2018-02-18 to its close. Should stops and targets use the stored high and low there once their reliability is checked?
9. **Multiple-testing method.** Approve Romano-Wolf stepdown on check and exam years plus PBO on learning years, with the global counter kept only for display?
10. **SELL and HOLD reference price.** The default is the close on the call date. Should a holder's actual entry price be used when known?
11. **NO_BUY at exactly zero.** NO_BUY is right only on a net loss (< 0), so a net of exactly 0 makes both BUY and NO_BUY wrong. Keep this?
12. **Overlapping calls.** May a symbol receive a new call while an earlier call on it is still open? Proposed: no, apart from SELL closing an open BUY.
13. **Score rounding.** Scores between bands (for example 79.5) need a rule. Proposed: round to the nearest integer, halves away from zero.
14. **Fee figures that could not be confirmed.**
    - **Commission:** the pre-July-2016 tiers and the exact 2016 effective date.
    - **SEBON fee:** whether it was cut after October 2023, and its level before 2020.
    - **DP charge:** whether the buy side pays it, and older amounts.
    - **CGT:** the 2026 rates.

    Conservative values are in use. Can the owner obtain the SEBON circulars?
15. **Cost on SELL calls.** A SELL is graded on price only. Should the holder's sell costs be charged, which would make a SELL right only if the fall exceeds the sell-side costs?
