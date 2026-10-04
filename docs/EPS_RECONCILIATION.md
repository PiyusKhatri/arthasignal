# EPS: Merolagani snapshots against Sharesansar statements

`docs/INFORMATION_SURVEY.md` §4 found 13 of 181 EPS pairs differing by more than 0.01: the latest Merolagani snapshot in `fundamentals` against the latest-quarter Sharesansar statement in `quarterly_report_figures`, same fiscal year. Code: `src/scrapers/eps_reconciliation.py` (rerun with `python -m src.scrapers.eps_reconciliation`). Output: `docs/eps_reconciliation.json`.

Evidence used for each pair:
- the full weekly Merolagani EPS history for that fiscal year (10 snapshots, 2026-07-23 to 2026-09-26);
- the Sharesansar statement's profit for the period and its number of outstanding shares;
- the 2082/83 bonus with its book-close date (`dividend_declarations`).

Every pair compared is a 4th-quarter report, so annualized and cumulative figures coincide. **Quarterly versus cumulative, and annualized, are ruled out as causes.**

## Result

| Category | Pairs | Rule |
| --- | ---: | --- |
| Match within 0.01 | 168 | — |
| Merolagani rescaled for bonus | 3 | Sharesansar = profit / outstanding shares, and Merolagani × (1 + bonus) = profit / outstanding shares, after the bonus book close |
| Merolagani rescaled for bonus (ratio only, no share count) | 2 | Merolagani × (1 + bonus) = Sharesansar |
| Sharesansar shows company-reported basic EPS | 3 | Merolagani = profit / outstanding shares; Sharesansar differs |
| No share count, no bonus | 2 | Cannot be classified |
| Unexplained | 3 | Neither figure equals profit / outstanding shares |

### 1. Merolagani rescales EPS retroactively after a bonus (5 pairs)

| Symbol | Merolagani history | Sharesansar | Profit / shares | Bonus, book close | Check |
| --- | --- | ---: | ---: | --- | --- |
| EBL | 32.35 (Q3) → **36.95** on 2026-08-22 → **35.19** on 2026-09-19 | 36.95 | 36.95 | 5%, 2026-09-17 | 35.19 × 1.05 = 36.95 |
| GBIME | 15.40 → **16.34** on 08-22 → **15.71** on 09-26 | 16.34 | 16.34 | 4%, 2026-09-21 | 15.71 × 1.04 = 16.34 |
| SADBL | 23.83 → **22.92** on 09-26 | 23.83 | 23.83 | 4%, 2026-09-24 | 22.92 × 1.04 = 23.84 |
| SMHL | 14.06 → **18.40** on 07-30 → **16.00** on 09-12 | 18.40 | — | 15%, 2026-09-08 | 16.00 × 1.15 = 18.40 |
| SNORL | **11.93** on 08-22 → **10.84** on 09-26 | 11.93 | — | 10%, 2026-09-24 | 10.84 × 1.10 = 11.92 |

In every case Merolagani first showed the reported EPS, equal to Sharesansar's, and then, two to four days after the bonus book close, replaced it with EPS on the enlarged share count. The company's report did not change. **Merolagani's EPS is therefore not point-in-time:** a snapshot taken after a book close shows a number that was never published as such. Used historically, it would mix later share counts into earlier dates.

### 2. Sharesansar shows the company's reported basic EPS (3 pairs)

| Symbol | Merolagani | Profit / outstanding shares | Sharesansar (reported basic EPS) | Implied share count / outstanding |
| --- | ---: | ---: | ---: | ---: |
| MBL | 17.49 | 17.49 | 15.44 | 1.133 |
| SANIMA | 26.14 | 26.14 | 24.92 | 1.049 |
| SBL | 23.79 | 23.79 | 21.84 | 1.089 |

Here Merolagani computes profit ÷ current outstanding shares. Sharesansar reproduces the bank's own "Basic Earnings Per Share" line, whose denominator is larger than today's outstanding shares (by 4.9-13.3%). That is consistent with a weighted-average or bonus-restated share count under the NFRS earnings-per-share rules (NAS 33). We have not seen the filings' notes, so the exact denominator is inferred, not verified.

### 3. Unexplained (3 pairs) and unclassifiable (2 pairs)

| Symbol | Merolagani | Sharesansar | Profit / shares | Note |
| --- | ---: | ---: | ---: | --- |
| NABIL | 28.36 | 27.74 | 29.22 | Neither equals profit / shares (implied denominators 1.030× and 1.053× outstanding). NABIL reports consolidated figures with two subsidiaries; whether either site used group profit attributable to the bank cannot be told from the stored data. |
| NMB | 20.18 | 19.53 | 20.81 | Same shape as NABIL (1.031× and 1.066×). |
| KSBBL | 22.75 | 22.34 | 23.15 | The Merolagani value has not changed since 2026-07-23, so it may still be the Q3 figure. |
| MANDU, MKHC | 21.24 / 1.58 | 23.36 / 1.59 | — | No share count in the statement (hydro layouts), no bonus. Not classifiable. |

"Standalone versus consolidated" is plausible for NABIL and NMB but is **not demonstrated**.

## Rule adopted (no parser change)

The parser reads both sites correctly. The differences come from what each site chooses to show.

1. **For research, use the company-reported EPS as first captured**: `quarterly_figure_captures.eps` with its `captured_at` (from Sharesansar's statement tab, which repeats the company's line). Do not use `fundamentals.eps` (Merolagani) for any historical or point-in-time purpose, because it is rewritten after bonus book closes.
2. When a comparable per-share figure is needed across firms, compute profit for the period ÷ outstanding shares from the same captured statement, and label it as computed, not reported.
3. The `fundamentals` table may still serve the live display of "current EPS", with the note that it is bonus-adjusted.
