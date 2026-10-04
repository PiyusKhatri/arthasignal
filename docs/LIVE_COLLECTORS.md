# Live text collectors

Built 2026-10-04. Code: `src/collectors/` (`store.py`, `mentions.py`, `news.py`, `social.py`, `run.py`, `classify_spec.py`). Tests: `tests/test_collectors.py`.

These sources gain value only from the day collection starts. A post collected later carries a later `first_seen_at`, so it cannot be used point-in-time for earlier dates. Backfilled history from portals that keep it is not clean either: an LLM label on old text is contaminated by look-ahead (`docs/RESEARCH_PLAN.md`). The news collectors were switched on today. The social collectors are built and wait for keys.

## Storage (append-only)

`text_items` holds one row per item version. The fields are:

- `source` and `channel`;
- `source_item_id` and `url`;
- `title` and `body` (raw text);
- `author_hash`: the SHA-256 of source and author. No handle is stored;
- `published_at` with `published_precision` (`second`, `minute`, `day` or `none`), as the source states it;
- `first_seen_at`: our own clock, which is the knowledge time;
- `language` (`ne` or `en` by script share);
- `symbols`: rule-based mentions;
- `content_sha256`, `retention`, `raw` (source metadata) and `collector_version`.

Rules:

- `UNIQUE (source, source_item_id, content_sha256)`. An edited item becomes a new row; nothing is overwritten.
- Triggers reject UPDATE, DELETE and TRUNCATE on `text_items` and on the run log `text_collector_runs`.
- **30-day sources** (YouTube, Reddit) keep their text in `text_items_ephemeral`, and `purge` deletes it after 30 days. The permanent row keeps only identifiers, timestamps, symbols, the hash and counts, and a CHECK forbids text in it.
- Row-level security hides both tables from `arthasignal_research`, because every row is dated inside the holdout. Live-only data is evaluated live.

**Point-in-time rule:** an item's knowledge time is max(`first_seen_at`, `published_at`) and, once labelled, max(that, `labeled_at`). A feature for signal date *t* may use only items known before that session's call deadline.

**Symbol mentions** (`mentions.py`) come from the active equity list (`companies`):
- tickers in uppercase;
- tickers in parentheses, after `$` or `#`, or after `NEPSE:`;
- company names without "Limited", "Ltd" and similar.

28 symbols that are English words (CITY, SHINE, UPPER, …) plus API, NRN, SBI and GDP count only when tagged. Nepali text names companies in Devanagari, which the rules do not map; that is left to the LLM labeller (`docs/LLM_CLASSIFICATION_SPEC.md`).

## Sources, in priority order

| # | Source | Method | Timestamp | Terms and robots | Status |
| ---: | --- | --- | --- | --- | --- |
| 1 | Sharesansar latest news | `/category/latest` list plus a `/newsdetail/…` page per new item | **Minute**, NPT, from the detail page (the list shows the day) | `robots.txt` allows all. Terms page renders with JavaScript and has not been read | **Running** |
| 1 | Merolagani news | `NewsList.aspx` plus `NewsDetail.aspx?newsID=` | **Minute**, NPT | No `robots.txt`. Terms not reviewed; treat as all rights reserved | **Running** |
| 1 | Arthasarokar | WordPress RSS `/feed` | **Second** (`pubDate`, UTC) | `robots.txt` blocks only SEO crawlers by name; RSS is published for syndication | **Running** |
| 1 | Bizmandu | WordPress RSS `/feed` | **Second** | `robots.txt` carries content-signal declarations but sets none for any use, so it neither grants nor restricts; no Disallow | **Running** |
| 1 | Kathmandu Post, Money section | Site RSS `/rss`, kept only for `/money/` links | **Day** (from the URL; the article page shows the day and the update time) | `robots.txt` allows all | **Running** |
| 2 | Telegram public channels | Official MTProto API via Telethon, as a user account | Second | **High risk.** The API terms forbid "using, accessing or aggregating data obtained from the Telegram platform to train, fine-tune or otherwise engage in the development, enhancement or deployment of artificial intelligence, machine learning models and similar technologies". Our end use (a meta-model, LLM labels) falls inside that wording. The Content Licensing and AI Scraping Terms (telegram.org/tos/content-licensing, checked 2026-10-04) go further: they prohibit "the scraping, indexing, harvesting, aggregation or use of data obtained from its platform" beyond ordinary use as a user, which covers systematic collection itself, through the API or the web preview | Built, **off** unless `ARTHASIGNAL_TELEGRAM_TERMS_ACK=1`. The classification spec refuses Telegram items |
| 3 | YouTube comments | YouTube Data API v3 with an API key: `channels`, `playlistItems` and `commentThreads` (1 unit each, out of 10,000 a day) | Second | Developer Policies: API data may be stored at most **30 days**. Text is kept 30 days and only derived fields persist | Built, needs a key and channel IDs |
| 4 | Reddit | Official OAuth API (application-only), `/r/{sub}/new` and `/r/{sub}/comments` | Second | Free for non-commercial use at 100 queries a minute. Developer approval required since 2026. Deleted content should not be kept, so text is kept 30 days | Built, needs keys, approval and subreddits |

Conduct:
- at least 3 seconds between requests per portal;
- a descriptive user agent;
- only the newest list page;
- detail pages only for new items, at most 40 per run.

Nothing collected is republished. Before any public or commercial use, the terms of Sharesansar and Merolagani must be read in a browser, and permission should be asked for (`docs/DATA_PLAN.md` already recommends this).

### Not collected, and why

- **Facebook** pages and groups: NEPSE promotion is heavy there, but there is no lawful collection path:
  - Meta's terms prohibit automated collection without permission;
  - group content requires a logged-in member;
  - the Groups API for third parties was withdrawn;
  - Page Public Content Access requires Meta App Review and business verification, and covers pages, not groups.

  Scraping would mean bypassing a login and breaking the terms, so it is excluded.
- **Viber communities, private Telegram groups, paid tip groups**: these require membership, often under false pretences. Excluded.
- **X/Twitter**: NEPSE discussion there is thin, and read access to the API is paid. Not built.

## First run (2026-10-04 10:46-10:47 NPT)

`python -m src.collectors.run news`: 56 items stored.

| Source | Items | With a symbol | Language | Precision | Published range (NPT) |
| --- | ---: | ---: | --- | --- | --- |
| sharesansar_news | 10 | 5 | en | minute | 2026-10-02 15:30 to 10-04 10:19 |
| merolagani_news | 8 | 2 | ne | minute | 2026-10-03 10:15 to 10-04 09:03 |
| arthasarokar | 20 | 0 | ne | second | 2026-10-01 19:00 to 10-04 09:55 |
| bizmandu | 15 | 0 | ne | second | 2026-10-03 16:34 to 10-04 10:44 |
| kathmandupost_money | 3 | 0 | en | day | 2026-10-03 to 10-04 |

- A second run immediately after stored 0 new items: each list was re-read, and no detail page was fetched again.
- The social collectors report `not_configured` and exit 5 when they are the only target.
- A bug found on the way: Merolagani detail pages send no charset, so they were decoded as Latin-1. The fetcher now defaults to UTF-8, and a test covers Nepali bodies.
- **Coverage gap:** Nepali-language portals gave 0 rule-based symbol mentions in 35 items. Rules alone will miss most company news in Nepali, and the LLM labeller is needed for it.

## Running from cron (not scheduled)

`scripts/cron/collectors.sh <target>` loads `.env.collectors` (gitignored) when present. It uses `flock` per target, writes the JSON report to `logs/collectors/`, and logs exit codes. Suggested server crontab:

```
*/30 * * * *  ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/collectors.sh news
15 * * * *    ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/collectors.sh social
30 3 * * *    ARTHASIGNAL_ROOT=/srv/arthasignal /srv/arthasignal/scripts/cron/collectors.sh purge
```

- Exit codes: 0 ok; 1 a source errored (the others still ran); 4 another run of that target is active; 5 nothing configured.
- **The daily purge is required** to keep within YouTube's 30-day limit. Any LLM labelling of YouTube and Reddit text must also happen within 30 days.
- Every 30 minutes is enough: the busiest list (Bizmandu) showed about 15 items in 18 hours. Missing a burst only delays `first_seen_at`; nothing is lost while the item stays in the list.

## API keys to create

Put every value in `/srv/arthasignal/.env.collectors` (never commit it), and the source lists in `config/social_sources.json`.

**YouTube Data API v3 (free):**
1. At console.cloud.google.com, create a project, for example `arthasignal-collectors`.
2. Under *APIs & Services → Library*, enable **YouTube Data API v3**.
3. Under *Credentials → Create credentials → API key*, then *Edit*: restrict it to YouTube Data API v3 and to the server's IP.
4. Set `YOUTUBE_API_KEY=...`.
5. List the channel IDs (`UC…`) of NEPSE commentary channels you choose under `youtube_channel_ids`. None are pre-filled: no list of channels has been verified.
6. Quota: each channel costs about 2 units per run plus 1-3 units per recent video, far below 10,000 a day.

**Reddit (free, non-commercial, needs approval):**
1. Log in with a dedicated account. At reddit.com/prefs/apps, choose *create another app*, type **script**, with redirect `http://localhost:8080`.
2. Submit Reddit's Data API access request for non-commercial research, as the Responsible Builder Policy requires.
3. Set:
   - `REDDIT_CLIENT_ID` (the string under the app name);
   - `REDDIT_CLIENT_SECRET`;
   - `REDDIT_USER_AGENT=server:arthasignal-research:v1.0 (by /u/<account>)`.
4. Add subreddits under `reddit_subreddits` after checking that they exist and are active (candidates: NepalStock, NEPSE, Nepal). Without credentials their existence could not be checked here.

**Telegram (free; prohibited by the terms for systematic collection, so do not enable without the channel owners' permission):**
1. With a dedicated phone number, log in at my.telegram.org, open *API development tools* and create an app, which gives `api_id` and `api_hash`.
2. Set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and `TELEGRAM_SESSION=/srv/arthasignal/secrets/telegram` (a path; the `.session` file is a credential and is gitignored).
3. Log in once interactively on the server to create the session: `.venv/bin/python -c "from telethon.sync import TelegramClient; TelegramClient('/srv/arthasignal/secrets/telegram', API_ID, 'API_HASH').start()"`. It asks for the phone number and code.
4. List public channel usernames under `telegram_channels`.
5. Only after deciding that the use complies with Telegram's API terms, set `ARTHASIGNAL_TELEGRAM_TERMS_ACK=1`. Under a strict reading, the text may not feed any ML model or LLM labeller. It could only feed rule-based counts that are inspected by people.

**LLM labelling:** no key is needed yet; nothing paid has run. See `docs/LLM_CLASSIFICATION_SPEC.md`.
