# Point-in-time rule for information sources

**The rule.** An item of information becomes usable at the **first moment it was observably public**. That moment is the earliest date among the sources whose date for that item passes verification. A date counts only if the check uses nothing but the item itself and rows its source created **before** it. The item is known from the **first session strictly after** that date, because no source records a time of day. Code: `src/backtest/knowledge_time.py`.

**What it replaces.** The old convention for quarterly reports (`info_eval`, ranker r1) was the *later* of the Sharesansar and Merolagani dates. Whether a report counted as known on day *t* then depended on a Merolagani row that might be published after *t*. The r1 audit caught this as a leak: 12 mismatches on 4 of 20 dates, all in the earnings features.

**Data used.** All measurements use stored rows published up to 2025-09-29, read on the research role. Nothing new was collected for this phase. Full output: `docs/point_in_time_reliability.json`.

## 1. Quarterly-report dates: how reliable is each source?

There is no independent clock for most items, so each source is checked against what it carries itself.

| Check | Sharesansar | Merolagani |
|---|---|---|
| Rows | 12,101 | 10,653 |
| Own creation clock | The URL slug ends in a creation date on 4,669 rows | Announcement IDs are assigned in creation order |
| Shown date earlier than its own clock | 1.97% of slugged rows by more than 1 day; worst 273 days. 97.7% are equal | 6.3% are more than 7 days before the median date of the previous 100 IDs |
| Back-dating by year | 0% in 2014-2019; 0.8-4.7% in 2020-2025 | 100% in 2009-2011 and 74% in 2012: old reports bulk-loaded with quarter-end dates. 3-7% in 2013-2015, 0.5-5.5% from 2016 |
| Dated more than 3 days before the quarter it reports on (impossible) | 0.09% (11 rows) | 0.14% (15 rows) |

**Agreement between the two sources** (9,558 reports in both):
- **Overall:** 62.5% have the same date, 89.1% are within 1 day and 91.4% within 7 days. The gap percentiles (Sharesansar − Merolagani) are 1%: −2, 50%: 0, 95%: +26 and 99%: +34 days.
- **Sharesansar earlier by more than 1 day:** 151 reports. The slug confirms Sharesansar's date for 20, contradicts it for 20, and is missing for 111.
- **Merolagani earlier by more than 1 day:** 887 reports. Merolagani's ID order confirms its date for 571 and contradicts it for 316. These are mostly Sharesansar's "company analysis" articles, which follow the report by weeks.

**NEPSE notices** could not be used as a reference: the nepalstock.com notice API answers `401 WARNING: UNAUTHORIZED ACCESS`, and access control is not bypassed.

## 2. Item-level verification

| Source | Knowledge date | Rejected when |
|---|---|---|
| Sharesansar | max(shown date, creation date in the slug when present) | Dated more than 3 days before its quarter end |
| Merolagani | max(shown date, median shown date of the previous 100 IDs) | Dated more than 3 days before its quarter end |

- **The quarter end** is the BS quarter end mapped to AD: Ashwin, Poush, Chaitra and Ashadh end are taken as 16 Oct, 13 Jan, 12 Apr and 15 Jul.
- **Merolagani's clamp** uses only IDs created earlier. A report cannot have been public before the reports its source created just before it, so back-dated bulk loads move to roughly their real upload date.
- **Sharesansar's clamp** likewise uses only the row's own slug.

**Report-level knowledge date:** the earliest verified date across sources, using the first non-correction announcement per source. Net profit still comes from the Sharesansar headline.

**Truncation-stable.** Every verified date depends only on the row and on rows created before it. Cutting the data at day *t* therefore keeps exactly the reports whose knowledge date is ≤ *t*, with the same dates (`truncate_reports`, tested).

**Effect** on the 11,109 reports with a net-profit headline published to 2025-09-29:
- 10,340 keep the Sharesansar date;
- 733 become known earlier, through a verified Merolagani date (median 18 days earlier);
- 36 become known later, through Sharesansar's own slug date;
- 13 have no verified date and are dropped.

## 3. Earnings-feature leak audit, rerun with the new rule

`python -m src.ranker.leak_audit` reran the r1 audit on the research role (41 s). It covers the same 20 seeded dates, every eligible symbol and all 45 features. Each feature is computed on the full development data and again on data cut at each date. Output: `docs/ranker_leak_audit_pit.json`.

| Rule | Mismatches | Leaky |
|---|---|---|
| Later of the two portals (r1, Phase E-C) | 12 on 4 of 20 dates, all earnings features | yes |
| Earliest item-verified source (this rule) | **0** | **no** |

The r1 model itself was not retrained or re-evaluated.

## 4. Other information sources

| Source | Date used | Verified how | Status |
|---|---|---|---|
| Prices, volume, turnover, index, breadth | Session close | Exchange session | Reliable by construction |
| Floorsheet and broker features | After that session's close | Contract numbers carry the session date (`docs/SIMULATION_PROTOCOL.md` section 4a) | Reliable. Source terms flagged (see the phase log) |
| Dividend declarations (Sharesansar table) | `announcement_date` | Must precede the book-close date | **Not reliable as a first-public date.** 74 of 890 (8.3%) are dated after their own book close, e.g. a batch dated 2020-07-29 for fiscal 2075/76. They are late, never early, so they are safe for look-ahead but delayed. Phase D replaces them with the earliest dated announcement |
| Corporate actions (`corporate_actions.action_date`) | Ex or book-close date, not the announcement date | — | Not a knowledge date. Phase D adds announcement dates |
| News (`text_items`) | `first_seen_at` (capture time) | Capture time is an upper bound | Live capture only: all rows are in the holdout period, so nothing is usable for development yet |
| Macro series (NRB, CDSC) | Publication date of the release | Release documents | Phase F |
