# Price Integrity: Corporate-Action Steps

Code: `src/backtest/price_integrity.py`. Raw outputs: `docs/price_integrity.json` (detector, calibration, recovery), `docs/price_integrity.steps.csv` (every step after recovery), `docs/price_integrity_windows.json` (excluded windows), `docs/recovered_corporate_actions.csv` (recovered rows with source). The data used runs from 2014-06-01 to **2025-09-29**. Nothing from the holdout (2025-09-30 onward) was read.

## 1. Detector

A **price step** is a close-to-close move (against the symbol's last traded close, whatever the gap) beyond the circuit band of that date plus 0.5 point of tolerance. The open is checked the same way from 2018-02-18, when real opens exist. Each step is classified:

| Kind | Meaning | Treated as |
| --- | --- | --- |
| `resolved_by_action` | a recorded bonus, right or dividend ex-session lies in the gap, and the adjusted move is inside the band | clean |
| `halt_resumption` | no recorded action and the stock had not traded for ≥ 20 sessions (suspension) | clean, a real repricing |
| `unresolved` | no action and no halt | **unresolved** |
| `action_mismatch` | an action is recorded, but the adjusted move is still beyond the band (wrong ratio or date, or a post-adjustment limit move) | **unresolved** |
| `adjustment_overshoot` | an action is recorded but the raw move was inside the band, so applying it creates a step | **unresolved** |

**Band history checked from data (2014-06 to 2025-09-29).** The 99.9th percentile of one-session moves on consecutive trading days is exactly 10.00% in 2024 and 2025, and the band is 10% throughout. Moves of 10-13% are spread evenly (about 45 per 0.5-point bin, no rounding cluster), so the 0.5-point tolerance is enough for tick rounding. Gaps do not widen the band: moves beyond 10.5% occur on 0.18% of consecutive days, 0.74% after 2-4 day gaps, 2.2% after 5-19 day gaps and **24%** after gaps of 20+ sessions. That is why 20 sessions defines a halt. **The 15% band from 2026-04-20 could not be confirmed from data without reading the holdout**; it is the existing code constant.

## 2. Calibration on symbols with stored corporate actions

Stored actions before 2025-09-29: 2,005 of the 2,189 records (the other 184 are dated inside the holdout and were not used), on 185 symbols. Truth = stored BONUS and RIGHT ex-sessions (927 with trades on both sides). A detection is a downward step within ±2 sessions.

| Measure | Value |
| --- | ---: |
| Downward steps detected | 588 |
| **Precision** (matched to a stored bonus or right) | **97.96%** (576/588) |
| Recall, all events | 63.1% (bonus 58.0%, right 92.1%) |
| **Recall, detectable events** (theoretical drop ≥ 15%, 383 events) | **97.4%** (bonus 96.5%, right 99.2%) |

Low overall recall is expected: bonuses below about 11% move the price less than the band allows and cannot be seen from prices at all.

**Can the ratio be recovered from prices? No.** On the 457 detected bonus events, the implied bonus (previous close / ex close − 1) against the stored ratio has a median absolute error of 2.9 points; only 33% are within 2 points, 74% within 5 points, and the 90th percentile error is 8.5 points. The ex-day's own market move (up to ±10%) is mixed into the step. **Adjustment ratios are therefore never inferred from prices**; unresolved steps are excluded, not adjusted.

## 3. Delisted symbols and recovery from public sources

Before recovery, delisted symbols had **0** stored corporate actions. The detector found 366 steps on 122 delisted symbols: 326 unresolved and 40 halts. Suspended symbols had 4 steps (3 unresolved).

For each of the 114 delisted or suspended symbols with an unresolved step, the dividend and right-share history was requested from Sharesansar's company pages (the same source and code as the existing scraper; 3 s between symbols):

| | |
| --- | ---: |
| Symbols attempted | 114 |
| Symbols with history found | **105** |
| Rows found / inserted (insert-only) | 1,014 / **1,012** |
| HTTP errors (company page missing) | 5 (ARUN, CLBSL, GFL, NBIL, RMFL) |
| Dividend rows skipped (no book-close date on the source) | 22 |

Merolagani was not used: earlier checks (Phase 4) showed it returns no data for delisted symbols. `corporate_actions` now holds 3,201 rows: 191 active, 103 delisted and 3 suspended symbols.

| Steps (2014-06 to 2025-09-29) | Before recovery | After recovery |
| --- | ---: | ---: |
| Resolved by an action | 534 | 817 |
| Halt resumption | 218 | 196 |
| Unresolved / mismatch / overshoot | 392 / 39 / 22 | 99 / 63 / 40 |
| **Total unresolved** | **453** | **202** |
| Delisted unresolved (symbols) | 326 (112) | 76 (45) |

Recovery added 2 new steps, which is why the total rises from 1,205 to 1,215. Mismatches and overshoots rose (39 → 63, 22 → 40) because recovered actions sometimes do not match the price series (date or ratio differences on the source). These stay excluded. Unresolved steps by year after recovery: 13, 14, 30, 29, 15, 24, 19, 14, 6, 9, 13, 16 (2014 to 2025).

## 4. Excluded holding windows

Every holding window (entry to exit) that spans an unresolved step is marked `data_error` and excluded from every horizon test (`src/scorecard/grading.build_market(..., unresolved=mask)`, `grading.unresolved_steps_mask`). Same-date universe, 2014-06-01 to 2025-01-19, after recovery:

| Horizon | Windows before | Excluded | After | Excluded share | v1 rule on the same data |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 410,170 | 623 | 409,547 | 0.15% | 0.11% |
| 10 | 408,929 | 1,099 | 407,830 | 0.27% | 0.18% |
| 20 | 406,437 | 1,967 | 404,470 | 0.48% | 0.32% |
| 40 | 401,449 | 3,649 | 397,800 | 0.91% | 0.61% |
| 80 | 391,481 | 6,766 | 384,715 | 1.73% | 1.16% |
| 120 | 381,543 | 9,594 | 371,949 | 2.51% | 1.67% |
| 160 | 371,485 | 12,051 | 359,434 | 3.24% | 2.16% |
| 240 | 351,409 | 16,395 | 335,014 | 4.67% | 3.06% |

Before recovery, the v1 replay excluded 0.4% (5 sessions) to 9.7% (240 sessions) of universe windows (`docs/SCORECARD_BASELINES.md`). The new detector is stricter than the v1 rule on the same data, because it also excludes mismatches and overshoots. The dev window contains 187 unresolved step sessions on 107 symbols.

## 5. Loud data-quality check

- `python -m src.backtest.price_integrity --check --end <date> --lookback N` exits **1** and lists the steps when any unresolved step lies in the last *N* sessions. On real data to 2025-09-29 with *N* = 60 (after recovery) it exited 1 with 6 steps. Five are action mismatches whose recorded action explains the drop but leaves an adjusted move of +10.7% to +13.5% (CITY, KKHC, NABBC, RFPL, SSHL), consistent with the post-adjustment rights rally seen earlier. One is unresolved: WNLBP −92.9% on 2025-07-15, a promoter-share line with no recorded action.
- `src/pipeline/data_quality.py`: new check `unresolved_price_steps` (last 20 sessions) in the daily health checks. `run_all_daily` sends a failure alert and raises `UnresolvedPriceStepsError` at the end of the run when it trips.

## Effect on earlier results

The 1,012 recovered rows change the price panels that every earlier study used (broker flow, events, scorecard v1). Those documents were produced on the pre-recovery data and were not rerun, except the scorecard under protocol v2.

## 6. Recent steps resolved (2026-10-04)

The six steps flagged by the check in the last 60 sessions to 2025-09-29 were each checked against Sharesansar (company dividend and right-share tables) and Merolagani (company page bonus and right-share sections). Output: `docs/price_integrity_recent_resolution.json`. Provenance is stored in the new table `corporate_action_sources` (symbol, date, type, value, source, URL, detail, verified time).

| Symbol | Step date | Raw move | Stored action | Sharesansar | Merolagani | Move from NEPSE adjusted base | Result |
| --- | --- | ---: | --- | --- | --- | ---: | --- |
| CITY | 2025-08-12 | −33.8% | right 80% (book close 2025-08-12) | right 1:0.8, 2025-08-12 | 1:0.8, FY 082-083 | +9.99% | resolved |
| KKHC | 2025-07-13 | −32.2% | right 100% (2025-07-11) | right 1:1, 2025-07-11 | 1:1, FY 082-083 | +10.00% | resolved |
| NABBC | 2025-08-25 | −40.9% | right 100% (2025-08-25) | right 1:1, 2025-08-25 | 1:1, FY 082-083 | +9.99% | resolved |
| RFPL | 2025-08-11 | −36.4% | right 100% (2025-08-11) | right 1:1, 2025-08-11 | 1:1, FY 082-083 | +9.99% | resolved |
| SSHL | 2025-08-03 | −25.7% | right 100% (2025-08-01) | right 1:1, 2025-08-01 | 1:1, FY 082-083 | +10.00% | resolved |
| WNLBP | 2025-07-15 | −92.9% | none | none | none (promoter share line) | −92.9% | **quarantined** |

**Why the five were flagged.** The actions were already stored and correct. On the ex-date NEPSE resets the reference price to the adjusted base, (previous close + 100 × right ratio) / (1 + right ratio) for a right and previous close / (1 + bonus) for a bonus, and the circuit band applies around that base. All five closed at the upper circuit from that base, with moves between +9.99% and +10.00%. The detector measured holder value instead: a rights holder pays Rs 100 per new share, so a 10% move from the base shows up as 10% + 10% × 100q / previous close, which comes to +10.7% to +13.5% here. The detector now also computes the move from the adjusted base (`adjusted_base`, column `base_move`) and treats a step with a recorded action as resolved if either the holder-value move or the base move is inside the band. That the five land at exactly +10.0% from the base confirms both the ratio and the ex-date.

**WNLBP** is a promoter-share line of Wean Nepal Laghubitta (Merolagani lists it under "Promotor Share"). It traded at 1,403 on 2025-07-14 and at 100 (par) the next session. Neither source has a bonus, right or dividend for WNLBP or WNLB, so nothing was inferred. It is in `price_quarantine` with the URLs checked. The live writer drops quarantined symbols from its calls and lists them in its daily output (`quarantined_symbols`, `quarantine_excluded_from_calls`). A quarantined step remains unresolved for grading, so windows that span it stay excluded.

**PRIN** (in the window, already resolved): Sharesansar records an 18% bonus and a 0.9474% dividend with book close 2025-07-18 for FY 2080/81. Merolagani's page shows only an 8.70% bonus for FY 081-082 and nothing for FY 080/81, so only the Sharesansar source is stored. The two sources do not confirm each other.

**Check rerun.** `python -m src.backtest.price_integrity --check` now exits 0 and prints the WNLBP quarantine. The check (and the daily `unresolved_price_steps` health check) still exits 1 or raises on any unresolved step that has no quarantine record. A new jump on a quarantined symbol on a different date still fails (test `test_quarantine_silences_only_the_acknowledged_step`).

**Effect over 2014-06 to 2025-09-29.** With the adjusted-base rule, unresolved steps fall from 202 to 159 (unresolved 99, overshoot 40 → 38, mismatch 63 → 22). Calibration on the current action table: precision 97.47%, recall 96.12% on 619 detectable bonus or right events, 65.2% on all. The horizon exclusion counts in section 4 and the matrix in `docs/MATRIX_V2.md` were computed before this change and were not rerun. They exclude slightly more windows than the new rule would.

**Holdout note.** While this was investigated, one ad hoc query listed WNLBP rows without a date bound and returned two rows dated after 2025-09-29. They were not used in any computation. Every query since has been bounded at 2025-09-29.
