# Simulation protocol v1.3

- **Version:** `sim-protocol-v1.3`, locked 2026-10-04.
  - **Replaces** `sim-protocol-v1.2` (commit a200731, config SHA-256 `7d961a97…2e37`).
  - **Earlier versions:** v1.2 replaced v1.1 (commit 2229c8d, `d495721a…3b20`), which replaced `sim-protocol-v1` (commit 702dcc0, `be906e7f…5656`). All stay in git history.
  - **Decisions:** the owner decided v1's 15 open questions in v1.1, Q16-Q20 in v1.2 and Q22 in v1.3. The changelog is section 15.
- **Constants:** every number and rule below lives in `config/simulation_protocol.yaml`. Code reads rules only from that file (`src/simulation/protocol.py`), and the SHA-256 of the file bytes is the protocol's identity. If the doc and the config disagree, the config is what runs, and the disagreement is a bug to fix in a new version.
- **Scope:** this phase locks the rules and implements only call grading and costs (`src/simulation/grading.py`, `src/simulation/costs.py`), tested on synthetic price paths. Nothing was built yet: no simulator, no score, and no study run on real data. The real-data reads were:
  - in v1, a count of trading sessions per month from `trading_calendar` (section 3) and a check of the trial table;
  - in v1.1, the floorsheet OHLC verification (section 4a): floorsheet Parquet files dated 2014-06-01 to 2025-01-19, and `daily_prices` over the same dates on the research role.
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
| long | 7-15 | 133 to 285 |

- **Each call carries** its horizon class and a maximum holding period in sessions inside the class range. The entry session is holding session 1.
- **No gap:** the classes meet at 57 and 133 sessions (decision 3).

## 4. Entry

- **Real opens:** entry is at the **open of the first session after the call date** (from 2018-02-18).
- **Before 2018-02-18:**
  - **The problem:** the stored open equals the previous close on 98.7-99.6% of rows every month (`docs/PHASE_LOG.md`, data survey), so it is not a price anyone could trade at.
  - **The rule (decision 8):** before 2018-02-18 the open, high and low come from the floorsheet (section 4a), and the close stays the stored close.
    - **Open:** the first board-lot trade by contract number.
    - **High and low:** the highest and lowest board-lot trade price, widened to the close when needed.
    - **Entry** is at that derived open of the next session.
  - **Fallback:** a bar with no derived values is reduced to its close (open, high and low replaced by the close), as in v1.
  - **Storage:** the derived bars live only in `~/Desktop/arthasignal-ai/derived/floorsheet_ohlc/bars.parquet`, flagged `source = floorsheet_derived`. `daily_prices` is never changed.
- **No fill:** there is no fill when the entry session has **no trade**, or is **locked at the upper circuit**. Locked means high = low and the close is within 0.5 point of the limit move from the previous close (10% limit, 15% from 2026-04-20, as in `src/backtest/event_study.py`).
- **No backfill:** an unfilled call is recorded as `unfilled` and never moved to a later session.
- **Settlement (decision 5):** a bought stock cannot be sold before it settles. The first session at which a sell is allowed is entry session + **3** (T+3).
  - **T+2 start date:** not confirmed from an official source, so T+3 stays in force for every date.
  - **What was found:**
    - SEBON approved the T+2 amendment of the Securities Transactions Clearing and Settlement Regulations 2069 in January 2021 and left the start to a CDSC notice ([Investopaper, 2021-01-10](https://www.investopaper.com/news/sebon-approves-t2-clearing-settlement-system/)).
    - Sharesansar reported a planned start of Magh 11, 2077, i.e. 2021-01-24 ([Sharesansar, 2021-01-15](https://www.sharesansar.com/newsdetail/t2-system-to-be-implemented-from-magh-11-2021-01-15)).
    - CDSC's own page says only that settlement "runs in a T+2 cycle" today, with no start date ([CDSC](https://cdsc.com.np/cmsettlementprocedure)).
  - **Once confirmed:** the official start date goes in a new version as a schedule row with 2 sessions, with its citation. The code already reads the lag by entry date (`Protocol.settlement_sessions`).

## 4a. Floorsheet OHLC verification (decision 8)

- **Code and output:** `python -m src.simulation.floorsheet_ohlc verify` writes `docs/floorsheet_ohlc_verification.json`, and `build` writes the derived bars. Both open only floorsheet files dated 2014-06-01 to 2025-01-19 (read-only) and refuse any end date in the holdout. `daily_prices` is read on the research role.
- **Window:** 2018-02-18 to 2025-01-19, where real OHLC is published.
  - 1,615 floorsheet files and 354,703 published symbol-days (796 more files before 2018-02-18 for the close check and the build).
  - 341,979 matched symbol-days with all trades; 341,745 with board lots only.
- **Acceptance thresholds,** fixed before the first run. A field is used only if all of these hold:
  - **Exact match** (within 0.5 paisa) on ≥ 90% of symbol-days;
  - **within 1%** on ≥ 98%;
  - **within 1% on ≥ 97%** after dropping a random 2% of floorsheet pages (3 seeds), which mimics the 1-2% of missing pages in 2015-2018.

  Each check is applied over the whole window and separately on the 15-digit contract numbers (2018-02-18 to 2018-11-05), the format used before 2018.

| Definition | Field | Exact | Within 1% | 15-digit exact / within 1% | Worst 2%-page-drop within 1% (all / 15-digit) | Passes |
|---|---|---|---|---|---|---|
| All trades (first run) | open | 98.4% | 99.1% | 93.1% / **97.4%** | 98.3% / **96.6%** | no |
| | high | 99.2% | 99.6% | 98.9% / 99.5% | 99.2% / 98.8% | yes |
| | low | 91.1% | **95.3%** | 92.5% / **95.6%** | **95.1%** / **95.3%** | no |
| Board lots, quantity ≥ 10 (used) | open | 99.5% | 99.8% | 95.9% / 99.2% | 99.0% / 98.3% | yes |
| | high | 99.8% | 99.9% | 99.8% / 99.9% | 99.4% / 99.2% | yes |
| | low | 99.8% | 99.9% | 99.9% / 99.9% | 99.6% / 99.5% | yes |

- **The board-lot rule was added after the first run, and both runs are reported.**
  - **What the first run showed:** with all trades, 99.7% of low mismatches had a derived low below the published one.
  - **The check:** in June 2024, dropping trades of fewer than 10 units (odd lots) raised the low match from 84.2% to 100% exact.
  - **Conclusion:** the published OHLC leaves odd lots out. Odd lots are 0% of floorsheet trades in 2014-2016, 1.4% in 2017 and 1.7% in 2018.
- **Contract numbers give trade order:**
  - **Numbers:** they are unique within each symbol-day (0 duplicates), and their first 8 digits equal the session date on every symbol-day.
  - **One segment per symbol-day:** from 2018-11-06 the number is 16 digits with a 2-digit segment, and no symbol-day spans two segments.
  - **Last trade against the published close:** with board lots, the last trade by contract number equals the published close on 99.7% of symbol-days (99.9% in the 15-digit period). Before 2018-02-18 it equals the stored close on 99.0% (99.3% within 1%) of 97,694 symbol-days.
  - **Against chance:** a randomly chosen trade of the day equals the published open on only 16.9% of symbol-days, so the contract order carries real information.
- **Effect of the missing trades:**
  - **By gap size:** symbol-days whose file reports a 1-2% or > 2% gap match the open exactly 96.9% and 96.3% of the time with board lots, against 99.6% at gaps below 1%. High and low stay ≥ 98.7%.
  - **Random page drops:** dropping 2% of pages lowers the open's within-1% rate by about 1 point in the 15-digit period, and it stays above the threshold.
  - **Before 2018:** the missing trades mean a derived open is the first *recorded* board-lot trade. When the first page is missing, the open can be a later trade; that is the main error left in the estimate.
- **Decision:** with board lots, open, high and low all pass every check, so all three are used before 2018-02-18 (`floorsheet_ohlc.use` in the config). With all trades, open and low would have failed.
- **The owner confirmed the board-lot definition in v1.2 (Q16).**
- **Derived bars:** 440,185 symbol-days from 2014-06-02 to 2025-01-19, of which 98,173 are before 2018-02-18. Only bars before 2018-02-18 are used.

## 5. Exit (BUY, and the counterfactual buys used for NO_BUY, WAIT and risk control)

- **Exit at whichever comes first:** target, stop-loss or horizon end. Checks start at the first session a sell is allowed.
- **Gaps:** an open at or through the stop or target fills **at the open**. Otherwise a low at or below the stop fills at the stop, and a high at or above the target fills at the target.
- **Both touched in one session:** the **stop is assumed first**, which is conservative.
- **Horizon end:** the close of the last holding session.
- **No trade, or locked at the lower circuit, when an exit is due:** the exit moves to the open of the next session that trades and is not locked down. The delay is recorded. If the path ends first, the call stays `pending` and is not graded.
- **Target and stop (decision 1):**
  - **Candidates:** two rule families, both compared on learning years only. The winner is frozen with the version before any check or exam run.
    - **Volatility:** stop = entry − *k* × ATR(14) and target = entry + *m* × ATR(14), using ATR at the call date.
    - **Swing levels:** stop just below the most recent swing low before the call date, and target at the most recent swing high above entry.
  - **Selection (Q17, v1.2):**
    1. the higher learning-year **call-accuracy edge over the same-date baseline** wins;
    2. ties go to the higher risk control score;
    3. only candidates with **PBO ≤ 0.3** qualify (`target_stop.max_pbo`).

    The general freeze limit for a version stays PBO ≤ 0.5 (section 13).
  - **Every BUY and HOLD must carry a stop.** The grader refuses either one without a stop.
  - Each call's target and stop are written and locked when the call is made.
- **A SELL closing an open BUY (decision 12):** the BUY exits at the open of the first session after the SELL call date at which a sell is allowed (`sell_call`). A stop or target reached first still wins, and so does an open that gaps through the stop. The exit is graded on its net like a horizon exit.

## 5a. Open calls (decision 12)

- **One open call per stock at a time.**
  - **What holds the slot:** a BUY, HOLD, NO_BUY or SELL is open from its call date until its exit, or until its last holding session when it has no exit.
  - **Blocked calls:** while a stock has an open call, any new call on it is not issued and is counted as blocked. The one exception is a SELL, which may close an open BUY.
  - **WAIT (Q18, v1.2):** it never holds the slot, and it is blocked like any other call while another call is open on that stock.
- **Positions (Q19, v1.2):** a filled BUY opens a simulated position.
  - **What closes it:** the BUY's own exit (target, stop or horizon end), or a SELL call that closes the open BUY.
  - **No position after the exit:** nothing stays open after the BUY resolves, so later calls on the stock never see a position.
- **Code:** `OpenCalls` in `src/simulation/grading.py`.

## 6. Costs (`src/simulation/costs.py`)

- **Trade size:** a fixed notional of **NPR 100,000** per call (decision 4), in whole shares (floor of notional / entry price, at least 1).
- **Per side:** broker commission at the tier rate for the whole amount (minimum NPR 10), plus the SEBON fee, plus the DP charge.
- **Capital gains tax:** the CGT rate times the positive net gain after both sides' costs. Short-term or long-term is set by calendar days held (> 365 is long).

| Item | Schedule used | Status and source |
|---|---|---|
| Commission before 2016-08-01 | flat 1.00% | **Not confirmed.** Sources give only "0.7-1 percent" before the July 2016 cut ([Kathmandu Post 2019](https://kathmandupost.com/money/2019/10/05/securities-board-of-nepal-mulls-lowering-stockbrokers-commissions)). Conservative top of the range; the exact July 2016 date was not found, so the old rate is kept to 2016-07-31 |
| Commission 2016-08-01 to 2020-12-26 | 0.60 / 0.55 / 0.50 / 0.45 / 0.40% for ≤ 50k / ≤ 5 lakh / ≤ 20 lakh / ≤ 1 crore / above | **Partly confirmed.** The 0.60-0.40 range is confirmed ([Himalayan Times 2020-12-27](https://thehimalayantimes.com/business/brokers-commission-slashed-by-33-per-cent-from-today)); the Kathmandu Post gives three tiers (0.6/0.5/0.4). The higher reading per tier is used |
| Commission 2020-12-27 to 2024-05-13 | 0.40 / 0.37 / 0.34 / 0.30 / 0.27% | Confirmed ([Himalayan Times](https://thehimalayantimes.com/business/brokers-commission-slashed-by-33-per-cent-from-today), [myRepublica](https://myrepublica.nagariknetwork.com/news/commission-for-brokerage-service-reduced-by-up-to-60-percent/)) |
| Commission from 2024-05-14 | 0.36 / 0.33 / 0.31 / 0.27 / 0.243% | Tiers 1, 2 and 4 confirmed ([Sharesansar](https://www.sharesansar.com/newsdetail/sebon-to-enact-revised-commission-rates-for-stockbrokers-implementation-begins-today-2024-05-14), [Investopaper](https://www.investopaper.com/news/stock-broker-commission-nepal/)). [Sharesansar 2024-05-07](https://www.sharesansar.com/newsdetail/sebon-announces-10-reduction-in-stockbrokers-commission-rates-effective-jestha-01-2024-05-07) gives tier 3 as 0.306% and tier 5 as 0.243%. The higher reading is used, so tier 5 rose from 0.24% in v1 to 0.243% |
| Minimum commission | NPR 10 per side | Current figure, secondary source ([Kharchapatra](https://kharchapatra.com/blog/nepse-share-trading-costs-nepal)); applied throughout |
| SEBON fee | 0.015% per side | **Not confirmed after October 2023, and not found before 2020.** The SEBON board decided on 2023-10-18 to cut it by 10% ([Merolagani](https://eng.merolagani.com/NewsDetail.aspx?newsID=96273)). [Sharesansar](https://www.sharesansar.com/newsdetail/sebon-announces-major-amendments-to-brokerage-commissions-and-regulations-2023-10-19) reports the new rate as 0.014%, pending Ministry of Finance approval; no circular or effective date was found. **0.015% is kept throughout (conservative)** |
| DP charge | NPR 25 per scrip per side | Rs 25 per company per settlement is confirmed for today. **Not confirmed** whether the buy side pays it: sources disagree, and the [CDSC settlement page](https://cdsc.com.np/cmsettlementprocedure) is silent. Older amounts were not found. **Charged on both sides throughout (conservative)** |
| CGT to 2021-07-15 | 5% | Section 95A, Income Tax Act, as amended by Finance Act 2068 ([KBC](https://www.kbc-ca.com.np/_uploads/capital-gain-tax.pdf)); "previously only 5%" ([NBSM budget note 2078/79](https://www.nbsm.com.np/uploads/large/1622709726242738.pdf)) |
| CGT 2021-07-16 to 2026-07-16 | 7.5% held ≤ 365 days, 5% longer | Confirmed (NBSM budget note 2078/79) |
| CGT 2026-07-17 to 2026-09-21 | 10% / 7.5% | Secondary sources only; not confirmed |
| CGT from 2026-09-22 | 5% / 3.75% | Gazette notice reported by [Sharesansar](https://www.sharesansar.com/newsdetail/capital-gains-tax-rates-reduced-decision-published-in-gazette-2026-09-22); [Nepal Press](https://nepalpress.com/2026/09/16/764315/relief-for-individual-investors-in-the-stock-market-capital-gains-tax-reduced-to-3-5-and-5-percent/) gives 3.5% long-term. The higher figure is used |

- **Examples at NPR 100,000:** a round trip costs about **1.06% plus CGT** before 2016-08 and about **0.71% plus CGT** from 2024-05.
- **Rates that only affect live calls:** the two 2026 CGT rows only touch live calls; they are recorded so the cost model stays one schedule.
- **Search for official circulars (decision 14):**
  - **Where:** sebon.gov.np (the circulars page and the 2015/16 annual report) and NEPSE.
  - **Found:** nothing that states any of the unconfirmed figures. The SEBON circulars page lists only recent circulars by addressee, and the 2015/16 annual report says only that a downward revision of brokerage commission was sent to the Ministry of Finance.
  - **Status:** every unconfirmed row keeps its conservative value and its `confirmed: false` flag. The documents to look for are listed in section 14.
- **Code:** `costs.sell_costs(shares, price, day)` gives the sell side alone, used for SELL grading.

## 7. Grading (`src/simulation/grading.py`)

- **The headline metric is call accuracy** = right calls / graded calls.
- **Statuses:** every call gets one of:
  - `graded`;
  - `unfilled`, for a BUY, which is counted wrong;
  - `pending`, when the path ends before resolution;
  - `ungraded`, for WAIT, for an unfilled NO_BUY, and for `reaches_holdout`.
- **Reference price (decision 10, narrowed by Q19 in v1.2):**
  - **HOLD and SELL** use the close on the call date.
  - **The one exception** is a SELL that closes an open BUY. It uses that BUY's entry price and shares (`Call.position_entry_price`, `position_shares`, built by `position_call`).
  - **Enforced in code:** the grader refuses position fields on any call type other than SELL.

| Call | Right when | Wrong when |
|---|---|---|
| **BUY** | Net profit after commission, SEBON fee, DP charges and CGT is **> 0**. Exiting above entry without reaching the target is right, and so is an exit forced by a SELL call with net > 0 | Net ≤ 0, so **net exactly 0 is wrong** (decision 11) and a 1-rupee net loss is wrong. Any **stop-loss exit** is wrong, even above entry. **Unfilled** (locked upper circuit or no trade) is wrong and counted in accuracy (decision 2) |
| **SELL** | shares × exit price < shares × reference − the holder's **sell-side costs** (commission, SEBON fee, DP charge on shares × reference, at the rates in force on the call date; decision 15). The exit price is the target, stop or horizon close. Shares are the closed BUY's, or NPR 100,000 / reference | Otherwise. At NPR 100,000 from 2024-05 the price must fall by more than 0.37% |
| **NO_BUY** | A buy at the next open held to horizon end (no target, no stop) would have **lost money** after all costs and tax (net < 0) | Net ≥ 0, so **net exactly 0 is wrong** (decision 11). If that buy could not fill, NO_BUY is `ungraded` and counted |
| **HOLD** | A **close at or above the reference** within the horizon, before any stop | Stop touched first (low at or below the stop; same-session ties go to the stop), or the horizon ends below the reference |
| **WAIT** | Not graded | - |

- **Missed opportunities on WAIT:**
  - **missed BUY:** a buy at the next open held to horizon end would have been right (net > 0);
  - **missed SELL:** the close at horizon end is below the reference.

  Both are reported per period.
- **Holdout (decision 7):** a call whose holding path would reach 2025-09-30 or later is `ungraded` with reason `reaches_holdout`, and is counted but never graded.
  - **Holding path:** its last holding session lies past the last development session, or an exit delay pushes it there.
  - **In code:** `grade(..., sessions_before_holdout=n)`, where *n* is the number of calendar sessions after the call date up to 2025-09-29.
  - **Guard:** the grader also refuses any path bar dated in the holdout.
- **Sealed grades (decision 7, widened by Q20 in v1.2):** a grade is hidden while it uses prices from a later exam year that has not run yet.
  - **Which grades:** every horizon class and every graded call type. The prices counted are the latest used by the grade, at its exit or at the hold-to-horizon counterfactual.
  - **"Later exam year":** an exam year that starts after the call date and is not the call's own period. When the prices reach several later exam years, the grade stays hidden until all of them have run.
  - **Examples:** a 2024 long call that uses 2025 prices, a 2023 long call that uses 2024 prices, and a December 2021 short call that uses January 2022 prices. A check-year (2020) call that uses 2021 prices stays hidden until `exam_2021` has run.
  - **Learning calls never use 2020 prices:** the learning embargo (section 10) stops them from being made, so no sealing is needed for them.
  - **In code:** `hidden(outcome, completed_runs)`.

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
  - **Long horizons (decision 6):** a long call's 133-285-session path can span more than one exam year.
    - **Long calls** are judged only on the **pooled exam years** (2021-2025 together, after the last exam has run). Their per-year figures are descriptive only.
    - **Short and mid** are judged per year and also reported pooled.
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
| Exam | 2021, 2022, 2023, 2024, then 2025-01-01 to 2025-09-29 (`exam_2025`), strictly in that order | Exactly one run per frozen version per year |
| Holdout | 2025-09-30 onward | Final verdict only |

- **Learning embargo (Q22, v1.3):** in a learning run, a call is made only when its whole holding period ends by **2019-12-31**.
  - **Calls that would cross into 2020** are **not made**. This is an embargo, not sealing: the call never exists, so there is no grade to hide.
  - **In code:** `learning_call_allowed(call, n)`, and `grade(..., sessions_before_learning_end=n)`, where *n* is the number of calendar sessions after the call date up to 2019-12-31. The grader refuses a crossing call and any path bar after 2019-12-31.
  - **Delayed exits:** a made call whose exit is delayed past 2019-12-31 (no trade or a locked lower circuit on its horizon day) is `ungraded` with reason `learning_embargo` and counted.
  - **The cost:** long calls (133-285 sessions) cannot be made after roughly the first half of 2018 or early 2019, and mid calls after mid-2019.
- **No back-testing on the same year:** a lesson learned from an exam year can only be judged on a **later** year. A version created after seeing exam 2022 may be run on 2023 and later, never on 2022 or earlier exam years.
- **2025-01 to 2025-09-29 is the last exam, `exam_2025` (decision 7).** It is the least-seen development window: no earlier study used prices after 2025-01-19.
  - **Holdout-reaching calls:** any call whose holding path would reach 2025-09-30 is left **ungraded** and counted. Outcomes may use prices after a period's end, but never past 2025-09-29. Most long calls made after about 2024-08 and nearly all mid and long calls in 2025 are lost this way.
  - **Sealed grades:** any grade that uses prices from a later exam year that has not run yet stays hidden until that year's exam has run, for all horizons (section 7).

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
- **Storage:** every run and every version is stored **immutably** with its config hash and code commit, and is never deleted. This protocol version is registered in `backtest_variant_trials`:
  - family `simulation_protocol`;
  - v1: parameters `{protocol: sim-protocol-v1, config_sha256: be906e7f…5656}`, fingerprint `7fc6c9e4…eb5f`;
  - v1.1: parameters `{protocol: sim-protocol-v1.1, config_sha256: d495721a…3b20}`, fingerprint `a2e192bd…57f4`;
  - v1.2: parameters `{protocol: sim-protocol-v1.2, config_sha256: 7d961a97…2e37}`, fingerprint `9d87c529…77e2`;
  - v1.3: parameters `{protocol: sim-protocol-v1.3, config_sha256: 22f2065e…0c89}`, fingerprint `1847392b…fe89`;
  - each was written by `python -m src.simulation.register` on the research role, idempotent. The server command is in `docs/PROD_DEPLOY.md` Part 11.

  Any change to the config changes the hash and needs a new version.

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

- **Decision (decision 9, approved as recommended):** the global penalty counter is still printed but no longer decides a pass.
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

v1's 15 questions were decided in v1.1, Q16-Q20 in v1.2 and Q22 in v1.3 (section 15). Still open:

21. **Fee documents to obtain (decision 14).** None of the unconfirmed figures was found in an official SEBON or NEPSE document online. These are the documents to look for:
    - **Commission before 2016-08:** the schedule of the Securities Businessperson (Stockbroker, Securities Dealer and Market Maker) Regulations 2064 (2008) as in force from 2014 to July 2016.
    - **2016 cut:** the Ministry of Finance-approved amendment to that schedule from the 2016/17 cut (about Shrawan 2073), with its effective date and tier table.
    - **SEBON fee after the 2023 cut:** the SEBON circular or regulation amendment that put the 10% SEBON fee cut (0.015% → 0.014%) into force, with its effective date, after the board decision of 2023-10-18 (Kartik 2080). Also the SEBON fee schedule in force before 2020.
    - **DP charge:** the CDSC tariff or SEBON-approved Depository Participant fee schedule stating whether the Rs 25 per scrip is charged on purchases as well as sales, and the amounts before the current one.
    - **CGT from 2026-07-17:** the Finance Act 2083 (Section 95A rates for listed securities), and the Nepal Gazette notice of 2026-09-22 with the reduced rates.
    - **T+2 settlement:** the CDSC notice that put T+2 into force under the SEBON amendment of the Securities Transactions Clearing and Settlement Regulations 2069 (January 2021).


## 15. Changelog

### v1.3 (2026-10-04): the owner's decision on Q22

22. **Learning embargo:** learning-year calls must have their whole holding period end by 2019-12-31. Calls that would cross into 2020 are not made in learning runs; this is an embargo, not sealing.
    - **Delayed exits:** a made call whose exit is delayed past 2019-12-31 is ungraded (`learning_embargo`).
    - **In code:** the grader refuses crossing calls and any 2020 bar in a learning run.

### v1.2 (2026-10-04): the owner's decisions on Q16-Q20

16. **Odd lots excluded (confirmed).** Trades of fewer than 10 units are excluded from the floorsheet-derived OHLC.
    - **Added after the first verification run.** On 2018-02-18 to 2025-01-19 (exact / within 1%):
      - **First run, all trades,** 341,979 symbol-days: open 98.4 / 99.1, high 99.2 / 99.6, low 91.1 / 95.3. On 15-digit contract numbers: open 93.1 / 97.4, high 98.9 / 99.5, low 92.5 / 95.6. With 2% of pages dropped, the worst within-1% rates were open 98.3 (15-digit 96.6), high 99.2 (98.8) and low 95.1 (95.3). Open and low failed.
      - **Second run, board lots,** 341,745 symbol-days: open 99.5 / 99.8, high 99.8 / 99.9, low 99.8 / 99.9. On 15-digit contract numbers: open 95.9 / 99.2, high 99.8 / 99.9, low 99.9 / 99.9. With 2% of pages dropped, the worst within-1% rates were open 99.0 (98.3), high 99.4 (99.2) and low 99.6 (99.5). All fields passed.
    - **Full output:** `docs/floorsheet_ohlc_verification.json`.
17. **Target/stop selection:** on learning years, by call-accuracy edge over the same-date baseline first, then risk control score, among rules with PBO ≤ 0.3.
18. **WAIT (confirmed):** it never holds a slot, and it is blocked while another call is open on that stock.
19. **Positions:** a BUY's exit (target, stop or horizon end) closes the simulated position. HOLD and SELL use the call-date close, unless a SELL closes an open BUY. The grader now refuses position fields on non-SELL calls.
20. **Sealing:** every grade that uses prices from a later exam year that has not run yet is hidden until that year's exam has run, for all horizons. This replaces v1.1's 2024-long-only rule.

### v1.1 (2026-10-04): the owner's decisions on v1's open questions

1. **Target and stop:** the candidates are ATR multiples and swing support/resistance levels, compared on learning years only, with the winner frozen before any check or exam run. Every BUY and HOLD must have a stop, and the grader enforces it.
2. **Unfilled BUY:** a locked circuit or no trades counts as **wrong**.
3. **Horizons:** short 1-3 months (19-57 sessions), mid 3-7 (57-133), long **7-15 (133-285)**, with no gap.
4. **Trade size:** NPR 100,000 per call.
5. **Settlement:** T+3 until the official T+2 start date is confirmed, then T+2 from that date with the citation. Not confirmed yet, so T+3 throughout. The lag is now read by entry date.
6. **Long horizons:** judged only on pooled exam years. Short and mid are also reported per year.
7. **Last exam and holdout:** 2025-01-01 to 2025-09-29 is the last exam, `exam_2025`. A call whose holding path would reach 2025-09-30 or later is ungraded. 2024 long-call grades that depend on 2025 prices stay hidden until `exam_2025` has run.
8. **Pre-2018 bars:** derived from the floorsheet; verified and adopted (section 4a), with the board-lot rule.
9. **Multiple testing:** Romano-Wolf stepdown on check and exam years, and PBO via CSCV on learning years. The global counter is printed but no longer decides a pass.
10. **HOLD and SELL reference:** the system's own entry price when it holds a simulated position, otherwise the close on the call date.
11. **Net exactly zero:** both BUY and NO_BUY are wrong.
12. **Open calls:** one open call per stock at a time, and a SELL may close an open BUY.
13. **Rounding:** scores round to the nearest integer, halves away from zero (`round_score`).
14. **Fees:** official SEBON and NEPSE sources were searched and none was found for the unconfirmed figures. The conservative values and flags stay.
    - **Tier change:** the 2024 commission tier 5 was raised from 0.24% to 0.243%, to the higher of the two published readings.
    - **New sources:** added for the SEBON fee (0.014% reported, not in force by any found circular) and for settlement.
15. **SELL costs:** SELL grading includes the holder's sell-side costs.
