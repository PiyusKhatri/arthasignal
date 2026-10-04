# Sector data-quality audit: non-equity instruments with equity sectors

Audit of 2026-10-04. Every research read ran as `arthasignal_research` on the development window. The holdout was not touched. The live floorsheet table could not be used, because all of its rows are in the holdout; the read-only floorsheet Parquet files were used instead.

## 1. How many non-equity rows carried an equity sector

**111.** All are active (status A). "Equity sector" means one of the 12 sectors in `SECTOR_TO_INDEX` (`src/pipeline/sector_index_mapping.py`). "Mutual Fund" and "Promoter Share" are not equity sectors.

| Instrument type | Sector it carried | Rows | Price rows 2014-06 to 2025-09-29 |
| --- | --- | ---: | ---: |
| Mutual Funds | Commercial Banks | 28 | |
| Mutual Funds | Life Insurance | 4 | |
| Mutual Funds | Development Banks | 2 | **17,157** (34 symbols) |
| Non-Convertible Debentures | Commercial Banks | 62 | |
| Non-Convertible Debentures | Development Banks | 8 | |
| Non-Convertible Debentures | Finance | 5 | |
| Non-Convertible Debentures | Investment | 2 | **16,812** (77 symbols) |

The examples from the request match: CSY, H8020, SIGS2, LSH12 and NICFC each carried the sponsor bank's name and "Commercial Banks". The full list with every change is in `docs/sector_fix_companies.json`.

## 2. Every place that computes per sector, and whether it filters to equities

**Research and scorecard code (`src/backtest`, `src/scorecard`, `src/ranker`, `src/league`).** All of it reads one price panel built by `src/backtest/event_data.py:28`, which joins `companies.instrument_type = 'Equity'`. Funds and debentures therefore **never entered** any of the following:

| Place | What it computes | Filtered? |
| --- | --- | --- |
| `src/scorecard/grading.py:105-107, 216-220` | Sector codes, then the same-date **sector median** used in mid-horizon correctness and baselines | Yes, via the panel |
| `src/scorecard/v2.py:73-74` | The sector component in v2 failure causes | Yes, via the panel |
| `src/backtest/event_study.py:160-162, 203, 378` | Sector benchmark returns for event trades | Yes, via the panel |
| `src/backtest/event_eval.py:147, 355` | Abnormal return against the sector; gate G4 | Yes, via the panel |
| `src/ranker/features.py:138-153` | Sector medians of `ret_20`, `ret_60`, `vol_20`; sector-relative features | Yes, via the panel |
| `src/league/bots.py:189-195` | At most 3 calls per sector | Yes, via the panel |
| `src/scorecard/situations.py` (the matrix) | Market states only; it uses the grading cube's sector median | Yes, via the panel |
| `src/backtest/price_integrity.py:290` | Universe (no sector statistics) | Yes, its own Equity join |

**App and pipeline code (`src/pipeline`, `src/services`, `src/api`):**

| Place | What it computes | Before the fix |
| --- | --- | --- |
| `src/pipeline/market_pulse.py:293-345` (price changes by sector) | Sector advance/decline and weighted change | Filtered (lines 305, 335) |
| `src/pipeline/market_pulse.py:348-367` (market caps by sector) | Cap weights | Filtered (357) |
| `src/pipeline/market_pulse.py:430-464` (sector turnover) | Sector turnover and its trailing average | Filtered (438, 455) |
| **`src/pipeline/market_pulse.py:467-485` (floorsheet by sector)** | **Sector broker concentration and coverage** | **Not filtered.** Fixed: line 476 |
| `src/pipeline/market_pulse.py:488-497` (sector symbol counts) | Coverage denominator | Filtered (494) |
| `src/pipeline/market_pulse.py:370` | Sector index performance | NEPSE's own published indices; not computed here |
| `src/pipeline/sector_fundamental_baseline.py:30` | Sector average P/E and P/B | Filtered |
| `src/services/nepse_quant_research.py:208-230, 251-262` | Sector breadth and turnover ratio | Filtered (225, 255) |
| **`src/services/nepse_quant_research.py:781-808`** (`build_quant_research`) | A symbol's **sector regime, sector index benchmark and sector P/E baseline** | **Not filtered.** `/intelligence/{symbol}` and quant research accept any symbol, so a fund got the Commercial Banks regime and baseline. Fixed with `equity_sector()` |
| **`src/services/quant_cross_sectional.py:35`** | A symbol's sector index benchmark (`relative_strength_sector_20d`) | **Not filtered.** Fixed |
| **`src/services/stock_intelligence.py:838, 950`** | The sector passed to the analysis payload and AI context | **Not filtered.** Fixed (the 950 path already lists equities only, line 857) |
| `src/api/stocks.py:257` | Sector baseline on the stock detail endpoint | Defensive only: `_load_company` (line 92) already restricts to Equity. Changed to `equity_sector()` |
| `src/api/market.py:194-203` | Stocks in a sector | Filtered (201) |
| `src/pipeline/train_quant_models.py:87, 105`; `validate_quant_walk_forward.py:48, 141`; `validate_quant_robustness.py:71-85`; `src/services/quant_features.py:165` | Sector index benchmark per training symbol | Filtered at symbol selection (87, 141) |

**Scripts and tests:**
- `scripts/test_nepse_scraper.py:24-26` only prints NEPSE's sector summary.
- Tests use synthetic panels.
- Neither computes sector statistics from the database.

## 3. How much the unfiltered places changed results

**Floorsheet broker concentration** (`docs/sector_fix_floorsheet_impact.json`). This recomputes the function's own logic on the last 56 floorsheet sessions up to 2025-01-19 (3,985,347 trades, 53,325 of them in non-equity instruments). Fund and debenture trades went into the **numerators** (broker totals, symbols covered), while the **denominators** (sector turnover, sector symbol count) were Equity-only:

| Sector | Non-equity share of sector floorsheet turnover (mean / max day) | Top broker's share of sector turnover, before → after | **"Coverage" of sector symbols, before → after** |
| --- | --- | --- | --- |
| Commercial Banks | 3.7% / **30.7%** | 19.39% → 17.74% | **311% (max 384%) → 100%** |
| Development Banks | 0.3% / 4.6% | 15.09% → 15.05% | 122% (max 144%) → 98% |
| Finance | 0.04% / 0.4% | 20.81% → 20.81% | 118% → 107% |
| Investment | 0.0% / 0.1% | 27.36% → 27.36% | 104% → 100% |

For Commercial Banks the app reported coverage of three to four times the sector, so its "coverage is broad enough to be indicative" flag was **always** on. The top broker's share was overstated by 1.65 points on average.

**Per-symbol context:** all 111 fund and debenture symbols, if requested through `/intelligence/{symbol}` or quant research, were scored against the sponsor sector's index, regime and P/E baseline. After the fix they get no sector context.

**Live data:** none of these app paths feed a research study or the scorecard, so no research number changed because of them.

## 4. Fixes and the loud check

- **Data:** the 111 rows now carry "Mutual Fund" (34) or "Debenture" (77) (`python -m src.database.fix_nonequity_sectors --apply`).
- **Future writes:** the company upsert (`src/pipeline/db_writers.py`) runs `normalize_sector()` after classification, so the API cannot reintroduce sponsor sectors.
- **Code:** the floorsheet query filters Equity and active. `equity_sector(instrument_type, sector)` (`src/database/instruments.py`) is used wherever a single symbol's sector drives a computation.
- **Loud check:** `nonequity_equity_sector` is in `check_daily_pipeline_health`.
  - `run_all_daily` alerts Discord and raises `NonEquitySectorError`.
  - `python -m src.pipeline.data_quality --sectors` exits 1, and it is a new step (`sectors`) in the production chain (`src/ops/daily.py`), so a failure is alerted.
  - Verified: before the fix it exited 1 listing C30MF, CSY, …; after it, exit 0.
- **Tests:** `tests/test_sector_universe.py` (6):
  - the normalization rules;
  - every `market_pulse` sector query filters `instrument_type`;
  - the floorsheet query in particular;
  - the loud check raises;
  - no non-equity symbol has an equity sector in the database;
  - the research universe and its panel sectors contain equities only.

  The suite passed 567 before the reruns.
- **Found on the way:** the research replay re-applied the v2.1 ledger schema through the research role, which tried to write the calendar rules (dated 2026-04-08), and the **holdout guard blocked it**. Research callers now skip the calendar sync (`apply_schema(..., calendar=False)`).

## 5. Reruns on the corrected universe

The research universe of record is Equity after the promoter-share reclassification of protocol v2.1. Funds and debentures were never in it, but promoter shares were (45 symbols with prices). Having no sector, they were pooled into the scorecard's "unknown" sector and counted in every universe median and mean. The reruns therefore measure the corrected universe against the last published results (`*_corrected.json`, `matrix_v2_corrected.json`, `info_results.json`).

**Event study** (`docs/event_results_equity.json`). Columns: trades, abnormal return by date, abnormal return versus sector, conservative low, verdict.

| | Before | After |
| --- | --- | --- |
| E1 | 992, +1.34%, +2.24%, −0.02% → fail | 992, +1.34%, +2.23%, −0.03% → **fail** |
| E2 | 968, −3.65%, −3.54%, −4.91% → pass | 968, −3.66%, −3.55%, −4.93% → **pass** |
| E3 | 10, −20.56% → fail | 10, −20.56% → **fail** |
| E4 | 173, −6.38%, −7.50%, −10.15% → pass | 169, −6.35%, −7.68%, −10.27% → **pass** |
| E5 | 4,486, −2.28% → fail | 4,415, −2.27% → **fail** |
| E6 | fail | **fail** |
| E7 | 4,352, −3.67% → fail | 4,287, −3.52% → **fail** |
| E8 | 4,094, −6.02% → fail | 4,038, −5.80% → **fail** |

**Broker flow** (`docs/broker_flow_results_equity.json`). Labelled rows 301,272 → 298,260. Columns: excess at 1% cost (by date), conservative low, gates passed.

| | Before | After |
| --- | --- | --- |
| H1 | −0.54%, −1.06%, 1 | −0.51%, −1.04%, 1 → fail |
| H2 | −0.78%, −1.13%, 1 | −0.78%, −1.12%, 1 → fail |
| H3 | −1.11%, −1.33%, 0 | −1.11%, −1.34%, 0 → fail |
| H4 | −0.83%, −1.06%, 1 | −0.85%, −1.07%, 1 → fail |
| H5 | −1.30%, −1.93%, 0 | −1.32%, −1.95%, 0 → fail |

**Earnings information** (`docs/info_results_equity.json`, calls written as `v1-eq`; the `v1` rows are untouched). Columns: win rate, baseline, edge, verdict.

| | Before | After |
| --- | --- | --- |
| I1 at 20 | 22.14%, 28.59%, −6.45 | 22.02%, 28.66%, −6.65 → NO EVIDENCE |
| I2 at 40 | 17.86%, 23.67%, −5.81 | 17.73%, 23.88%, −6.15 → NO EVIDENCE |
| I3 | −6.91 | −6.93 → NO EVIDENCE |
| I4 | −7.22 | −7.43 → NO EVIDENCE |
| I5 | −10.34 | −10.82 → NO EVIDENCE |
| I6 | +2.46 (130 calls) | +2.48 → INSUFFICIENT SAMPLE |

**Situation matrix for model v0** (`docs/matrix_v2_equity.json`). Replay calls were written as `model_v0` **v0.1** on the corrected universe; 20,944 calls became 20,865.

| | Before (v0, `accuracy-v2-pi2`) | After (v0.1, `accuracy-v2`) |
| --- | --- | --- |
| Verdicts | 0 PASS, 38 NO EVIDENCE, 66 INSUFFICIENT | **0 PASS, 38 NO EVIDENCE, 66 INSUFFICIENT** |
| Cells that changed verdict | | **none** |
| Largest edge change in any cell | | 0.9 points (`pre_book_close` at 80 sessions) |
| Unresolved-step sessions in the panel | 156 | 117 |
| Edge at 5 / 10 / 20 / 40 / 80 sessions | +2.05 / +1.99 / +1.89 / +1.89 / +0.71 | +2.29 / +2.24 / +2.08 / +1.99 / +0.89 |
| `new_listing` at 40: edge, plain bound, penalized bound | +8.78, +1.09, −6.37 (*K* = 520) | +8.89, +2.06, −7.01 (*K* = 4,264) |
| Mean same-date baseline at 5 / 20 / 40 sessions | 29.23% / 30.11% / 23.28% | 29.36% / 30.26% / 23.44% |

The penalized bound moved mostly because *K* grew from 520 to 4,264: there are now 41 versions in the family, including the ranker trials and this v0.1 replay. It did not move because of the universe.

## Does any earlier conclusion change?

**No.**
- The event study still has exactly two passes, the avoid rules E2 and E4, with nearly identical estimates.
- All five broker-flow and all six earnings hypotheses still fail.
- The v0 matrix is still 0 PASS. `new_listing` at 40 sessions remains the only cell above +8 points and still fails its penalized bound.
- The fund and debenture contamination affected only the app's market-pulse broker statistics and the per-symbol context of non-equity symbols. Neither feeds any study. Those were real errors, and they are fixed.

## Follow-up (2026-10-04): equities without a sector

The 51 are actually **54** Equity rows without a sector, all delisted (three have no development-window prices). `python -m src.scrapers.sector_sources` fetched each symbol's Sharesansar and Merolagani company pages (3 s apart) and stored every raw label append-only in `company_sector_sources` (106 rows: source, URL, HTTP status, page title, label). A sector was assigned only when the sources were unambiguous:
- exactly one equity sector between them;
- "Merged", blank and missing count as no information;
- a debenture label stops the assignment.

Each assignment is in `company_sector_assignments` with its evidence. The full output is in `docs/sector_sources.json`.

- Sharesansar shows **"Merged"** for merged companies, which is a status, not a sector. Merolagani still shows the business sector, so all 36 assignments come from Merolagani.
- Two Merolagani labels were mapped only after checking Merolagani's own taxonomy on listed companies:
  - "Development Bank Limited" → Development Banks (CORBL, EDBL, GBBL and GRDBL, 4 of 4);
  - "Non-Life Insurance" → Non Life Insurance (HEI, IGI, NICL and NIL, 4 of 4).
- ILFCM is named a microfinance, but Merolagani lists it as "Development Bank Limited". It was assigned Development Banks, as the source says.

| Outcome | Count | Symbols |
| --- | ---: | --- |
| Assigned | **36** | Development Banks 9, Microfinance 9, Non Life Insurance 7, Life Insurance 5, Finance 3, Commercial Banks 2, Hydro Power 1 (each symbol is listed in the JSON) |
| No source names a sector | **14** | ARUN, BOK, CLBSL, DIYALO, KMBL, KMBSL, NGBBL, NICAD 85/8, NIFRAUR85/, NLBSL, NMBMB, RMFL, UMB, WMBF |
| Sources say it is a debenture | **3** | ADBLB, ADBLB86, ADBLB87 (stored as Equity; no development-window prices; instrument type left unchanged pending review) |
| Empty symbol in `companies` | **1** | `''` (16 price rows in 2014-2015) |

**18 remain without a sector.** The scorecard's pooled "unknown" sector shrinks from 51 symbols (31,097 development-window rows) to 15 (3,787 rows). Three of those (`''`, NICAD 85/8, NIFRAUR85/) fail the research panel's symbol format anyway, so research keeps 12 sectorless symbols (3,694 rows). The earlier reruns were **not** repeated with the new sectors. They affect only the mid-horizon sector median of these delisted names.

The server gets the same 36 assignments from the committed evidence, without network access, through `deploy/migrations/m002_equity_sector_assignments.py`. It only fills sectors that are still empty and leaves any contradiction untouched.

## Still open (not fixed here)

**Previously: 51 Equity symbols had no sector** (31,097 development-window price rows). The scorecard pools them into one "unknown" group (`grading.py:105`), and that pooled group's median is their sector benchmark at mid horizons. Assigning sectors needs a source for each symbol, and changing the grading rule would be a protocol change, so neither was done.
