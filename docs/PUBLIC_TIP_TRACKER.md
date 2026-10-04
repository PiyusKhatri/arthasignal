# Public tip tracker

**Declared:** 2026-10-04 11:08:40 Nepal time (05:23:40 UTC), before any tip was collected. **Code:** `src/tips/` (`parser.py`, `sources.py`, `store.py`, `run.py`). **Tests:** `tests/test_tips.py`. **Registry:** `backtest_variant_trials`, in family `public_tips_v1` and in `scorecard_accuracy_v2`. The fingerprints are listed in `docs/PHASE_LOG.md` (Phase D).

The tracker records buy and sell tips that NEPSE tip channels and analysts post publicly. Each tip is stored immutably and graded with the same scorecard and protocol v2 as our own bots. That answers two questions:

1. How accurate are public tips really?
2. Are promoted stocks an avoid signal?

## Sources, and what is not collected

| Source | How | Status |
| --- | --- | --- |
| YouTube channels | The official Data API v3 with an API key. Only the **channel's own videos** (title and description) are read, never comments, so no individual viewer's data is touched. Text is kept 30 days (YouTube Developer Policies), and the tip record keeps only the symbol, direction, levels, timestamps and video id | Built; needs `YOUTUBE_API_KEY` and `youtube_channels` in `config/tip_sources.json` |
| Public web pages of analysts | Plain HTTP fetch, only when `robots.txt` allows it for our user agent; at least 3 s between pages. A new row is stored only when the page changes. Tips found on an unchanged section are not duplicated | Built; needs `web_pages` in the config |
| Manual entry | `python -m src.tips.run add --platform youtube\|web\|other --channel NAME --url URL --posted-at ISO --text "..."`, for a tip seen on a public page | Built |
| **Telegram public channels** | **Not collected automatically.** Telegram's Content Licensing and AI Scraping Terms prohibit "the scraping, indexing, harvesting, aggregation or use of data obtained from its platform" beyond ordinary use as a user (telegram.org/tos/content-licensing). That covers the API and the no-login web preview (`t.me/s/…`) alike. A Telegram tip can be entered manually only with `--consent-ref` recording the channel owner's permission. Without it the command refuses (exit 6) | Blocked by terms |
| Facebook pages | Not built. The only lawful route is Meta's Page Public Content Access, which needs App Review and business verification. Groups are not possible | Not built |
| Private or paid groups, anything behind a login | Never | Excluded |

**Personal data:**
- Tip rows store only the **public channel name** (for YouTube, the channel title the API returns) and the post URL.
- No author handle, member, commenter or phone number is stored.
- `author_hash` is left empty for every tip source.
- The general Telegram collector from Phase C no longer stores post signatures either.

**No sources were invented.** `config/tip_sources.json` is empty, and you choose the channels and pages.

## Parsing (rule-based, frozen as `p1`, no ML or LLM)

`src/tips/parser.py` turns text into tips:

- symbols come from the active list (`mentions.py`);
- direction comes from English and Nepali verbs:
  - buy: buy, accumulate, long, किन्नुहोस्, खरिद गर्नु;
  - sell: sell, exit, book profit, short, बेच्नुहोस्;
  - avoid: avoid, don't buy, नकिन्नुहोस्. These count as sell-side;
- entry (`@ 500-510`), target (`Target 560`, `लक्ष्य ५६०`) and stop (`SL 480`) are read when present; Nepali digits are converted.

The parser is conservative:
- questions are dropped (many video titles are "NABIL किन्ने कि नकिन्ने?");
- "hold" and "don't sell" are dropped;
- segments with more than 5 symbols are dropped as market summaries;
- a symbol tipped both ways in the same post is dropped;
- a target is dropped if it is below the entry or more than 3× the entry, and a stop if it is above the entry;
- a post laid out as a block (symbol, then "Buy @", then "Target") is read as one tip.

It uses no ML, so it is consistent with the terms of every source.

**Measured so far:** run over the 56 real news articles collected in Phase C, the parser produced **0 tips**, so no false positives on news. Its recall and precision on real tip posts are **unknown**, because no tip source is configured yet. Before any verdict is quoted, hand-check 100 parsed posts against the stored text.

## Timing and the ledger

- **Signal date:** the session before the first market open (11:00 NPT) after the tip's **posting time**. Posting time is used when the source gives it to the second or minute (YouTube, manual entry). Otherwise, as for web pages, it is the time we first saw the tip. Entry is at that open, under v2 rules.
- **Write window:** v2's ledger CHECK requires a live call to be written before 11:00 NPT on the calendar day after its signal date. A tip we did not capture in time is stored and marked `outside_write_window`, and it is never graded or backdated. Running `cycle` hourly, and once before 10:30, keeps that loss small on trading days.
- **Known limitation:** a tip posted on a Friday, Saturday or holiday maps to the previous trading day's signal date, whose window has already closed. Such tips can never be graded under the unchanged v2 CHECK. Their count is visible in `public_tip_events`. Fixing this needs a protocol amendment (deadline = the next session's open), which is your decision. It has not been made.
- **Each graded tip writes these live rows** to `scorecard_calls` (`probability` NULL; the situation labels of its signal date):

  | Row | Strategy | Version | Graded as |
  | --- | --- | --- | --- |
  | Per channel | `tip_<platform>_<channel>` | `p1-buy` or `p1-sell` | Buy, or avoid when it is a sell |
  | Aggregate | `tips_all` | `p1-buy` or `p1-sell` | Buy or avoid; one row per symbol and date across channels |
  | Promoted | `tips_promoted_avoid` | `p1` | Avoid; written for buy tips only |

- **Events** (`public_tip_events`, one per tip): `written`, `duplicate_same_day`, `outside_write_window`, `unknown_symbol` or `quarantined`. A tip with no event is still pending (its session has no prices yet).
- **Tables:** `public_tips`, `public_tip_events` and `tip_leaderboard` reject UPDATE and DELETE. Row-level security hides all three from the research role.

## Grading and leaderboard

`cycle --grade`, run in the evening after prices, does the following:

- grades matured tip calls with `grade_calls_v2`, exactly as for the bots;
- writes two leaderboards to `tip_leaderboard` at 5, 10 and 20 sessions, using the league code. The columns are edge, bounds, expectancy, excess expectancy, cohort drawdown, coverage, folds and the verdict:
  - **aggregates** (`tips_all`, `tips_promoted_avoid`), with *K* from `scorecard_accuracy_v2`;
  - **per channel**. These verdicts are **descriptive report cards**, with *K* = tracked channel versions × 104. They are not claims for any of our systems;
- writes a **target and stop report** per channel for buy tips with levels, at 5, 10 and 20 sessions: the target hit first, the stop hit first, neither, or pending. It uses raw daily highs and lows, counts the stop first when both are touched on the same day, and excludes windows with a corporate action. It is supplementary and not part of v2.

## Declared hypotheses (live calls only, protocol v2 unchanged)

| ID | Strategy / version | Side | Hypothesis | Horizons |
| --- | --- | --- | --- | --- |
| T1 | `tips_all` / `p1-buy` | buy | Public buy tips beat the same-date baseline by ≥ +8 points. Expected to fail; this measures the tipsters | 5, 10, 20 |
| T2 | `tips_all` / `p1-sell` | avoid | Stocks that public tips say to sell or avoid trail the baseline | 5, 10, 20 |
| A1 | `tips_promoted_avoid` / `p1` | avoid | **Promoted stocks are an avoid signal**: a stock with a public buy tip known before the deadline is an incorrect buy, with edge ≥ +8 points under the mirrored avoid grading, and it trails the universe at 1% cost | 5, 10, 20 |

- A1's edge is minus T1's edge, measured on deduplicated stock-days. Registering both is deliberate: the avoid gate (avoided stocks must trail the universe) differs from the buy gate.
- Registering three more versions in `scorecard_accuracy_v2` raises *K* to 14 × 104 = 1,456.
- **Minimum sample:** 40 independent windows at 5-20 sessions. How often tips arrive is unknown until sources are configured. Even with a tip in every window, a claim at 5 sessions needs about 240 trading sessions (a year), and one at 20 sessions about 3.7 years.

## Running it (not scheduled)

`scripts/cron/tips.sh` (flock; JSON reports in `logs/tips/`):

```
5 * * * *     ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/tips.sh cycle
25 10 * * 0-4 ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/tips.sh cycle
55 17 * * 0-4 ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/tips.sh cycle --grade
```

The 17:55 run must follow the day's price ingestion. When there is nothing to write or grade, `cycle` does not load price data at all.

**Dry run today** (`python -m src.tips.run cycle --dry-run`):
- YouTube and web: `not_configured`;
- Telegram: `blocked`, with the terms reason;
- 0 open tips, so no price data was loaded and nothing was written.
