# LLM classification spec (text-labels-v1)

A specification only. **No paid API has been called.** Code: `src/collectors/classify_spec.py`, which holds the label schema, prompts, validator, label table DDL and knowledge-time rule; tests are in `tests/test_collectors.py`. The provider and model are deliberately not fixed. Each model is a separate *labeler version*, and changing it starts a new label series.

## What is labelled

Each `text_items` row from an allowed source gets one JSON object:

| Field | Values | Use |
| --- | --- | --- |
| `market_relevant` | bool | Filters out politics and weather from the portals |
| `symbols[]` | `{symbol, role: subject or mentioned, confidence}`, restricted to the active symbol list | Maps Nepali company names to symbols, which the rules cannot do |
| `event_type` | earnings, dividend_bonus, rights_fpo_ipo, agm_book_close, merger_acquisition, regulatory_action, monetary_policy, management_change, project_operations, price_movement_report, promotion_tip, rumor, market_commentary, macro, other, none | Event features; the news system A5 |
| `sentiment` | `direction` −2 to +2, `confidence` | The direction as the text presents it, not a prediction |
| `promotion` | `is_promotion`; `signals` ⊂ {target_price, urgency, guaranteed_return, coordinated_buy_call, insider_claim, paid_group_invite, circuit_prediction, hold_and_dont_sell}; `confidence` | The pump and promotion detector A6, tested as an **avoid** signal |
| `novelty` | new_information, repeat, recap, unknown | Separates first reports from re-posts |
| `evidence` | ≤ 240 characters copied from the text | Audit trail; must be a substring |
| `language` | ne, en, mixed, other | |

The prompt (`SYSTEM_PROMPT`, `USER_TEMPLATE`) tells the model:
- to use only the given symbol list;
- not to guess;
- to copy its evidence from the text;
- not to use knowledge of what happened after publication.

The prompt hash (`prompt_sha256()`) is stored with every label.

## Sources

- **Allowed:** the five news portals, YouTube videos and comments, and Reddit posts and comments.
- **Blocked:** Telegram. `build_prompt` raises `PermissionError`, because Telegram's API terms forbid using its data in developing or deploying AI or ML models.

## Storage and point-in-time rules

- `text_labels` is append-only, with `UNIQUE (item_id, spec_version, labeler, model_id, prompt_sha256)`. Fields: labels, `valid`, tokens, cost and `labeled_at`. The DDL is in `LABEL_TABLE_DDL`; it is not applied yet.
- A label that fails `validate()` is stored with `valid = false` and never used. Failures include an unknown symbol, a bad enum, an evidence string that is too long or a missing field.
- Knowledge time = max(`first_seen_at`, `published_at`, `labeled_at`). A label made after a call deadline cannot feed that day's call. The labelling job therefore has to run between collection and the league's evening run.
- YouTube and Reddit text has to be labelled within 30 days, before `purge` removes it.
- **Look-ahead contamination:** a general LLM has read the news of its training period. Labels of items published before the model's training cutoff are not used as evidence. In practice, only items with `first_seen_at` ≥ 2026-10-04 that were labelled live count.

## Validation before any label is used

1. Hand-label a gold set of 300 items, stratified by source and language (Nepali and English), double-coded by two people where possible.
2. Per field, report agreement with the gold set (macro F1 for `event_type`; precision and recall for `symbols` subjects and `is_promotion`).
3. Thresholds before a field may feed any analysis system:
   - `symbols` subject F1 ≥ 0.80;
   - `event_type` macro F1 ≥ 0.70;
   - `is_promotion` precision ≥ 0.80.
4. Re-run the gold set whenever the labeler, model or prompt changes. Each change is a new labeler version, with its own label series.
5. Track daily: the share of labels with `valid = false`, and drift in the event-type mix.

## Cost (to be measured; nothing has run)

Input per item ≈ the fixed prefix (instructions plus the active-symbol list, about 312 symbols × ~10 tokens ≈ 3,000-3,500 tokens) plus the item text (≈ 200-1,000 tokens). Output ≈ 150 tokens.

- The fixed prefix is identical for every item. A provider with prompt caching cuts most of the input cost.
- **Volume measured today:** the five portals listed 56 items covering about 1-3 days each. That suggests roughly 40-80 news items a day, to be measured after a week.
- Monthly cost ≈ items per day × 30 × (input tokens × input price + output tokens × output price).
  - For 60 news items a day at 4,000 input and 150 output tokens, that is 7.2 M input and 0.27 M output tokens a month.
  - At an illustrative $1 per million input and $5 per million output, that is about **$8.6 a month** before caching.
  - Social comments could be 10-20 times the volume, so the social sources should be labelled only when a rule-based symbol mention exists or the text matches promotion keywords.
- A one-off backfill of historical portal news (Merolagani has news since about 2016) would be 100k+ items. That is about $400+ at the same prices, and it is not clean evidence (see above). Not recommended.

## Not built yet

The labelling job itself: calling an API, validating, inserting and accounting for cost. It is the next step once a provider and budget are chosen. It must:
- refuse Telegram items;
- write only to `text_labels`;
- never edit `text_items`.
