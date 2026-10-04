from __future__ import annotations

import hashlib
import json
from typing import Any

SPEC_VERSION = "text-labels-v1"

EVENT_TYPES = (
    "earnings", "dividend_bonus", "rights_fpo_ipo", "agm_book_close", "merger_acquisition", "regulatory_action",
    "monetary_policy", "management_change", "project_operations", "price_movement_report", "promotion_tip", "rumor",
    "market_commentary", "macro", "other", "none",
)
PUMP_SIGNALS = (
    "target_price", "urgency", "guaranteed_return", "coordinated_buy_call", "insider_claim", "paid_group_invite",
    "circuit_prediction", "hold_and_dont_sell",
)
SOURCES_ALLOWED = ("sharesansar_news", "merolagani_news", "arthasarokar", "bizmandu", "kathmandupost_money", "youtube_video",
                   "youtube_comment", "reddit_post", "reddit_comment")
SOURCES_BLOCKED = {"telegram": "Telegram API terms forbid using platform data in developing or deploying AI or ML models"}

LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["market_relevant", "symbols", "event_type", "sentiment", "promotion", "novelty", "evidence", "language"],
    "properties": {
        "market_relevant": {"type": "boolean"},
        "symbols": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["symbol", "role", "confidence"],
                "properties": {
                    "symbol": {"type": "string", "pattern": "^[A-Z0-9]{2,20}$"},
                    "role": {"enum": ["subject", "mentioned"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "event_type": {"enum": list(EVENT_TYPES)},
        "sentiment": {
            "type": "object",
            "additionalProperties": False,
            "required": ["direction", "confidence"],
            "properties": {"direction": {"type": "integer", "minimum": -2, "maximum": 2},
                           "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
        },
        "promotion": {
            "type": "object",
            "additionalProperties": False,
            "required": ["is_promotion", "signals", "confidence"],
            "properties": {"is_promotion": {"type": "boolean"},
                           "signals": {"type": "array", "items": {"enum": list(PUMP_SIGNALS)}},
                           "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
        },
        "novelty": {"enum": ["new_information", "repeat", "recap", "unknown"]},
        "evidence": {"type": "string", "maxLength": 240},
        "language": {"enum": ["ne", "en", "mixed", "other"]},
    },
}

SYSTEM_PROMPT = """You label short texts about the Nepal Stock Exchange (NEPSE) for a research database.
Return only JSON that matches the given schema. Do not guess: if the text does not name a listed company, return no symbols.
Use only the symbols in the provided list; map company names written in Nepali or English to them.
role = subject when the text is mainly about that company, mentioned otherwise.
event_type is the single main event. Use promotion_tip for buy or sell calls by anyone other than the company or regulator.
sentiment.direction is the likely price implication for the subject symbols as the text presents it: -2 strongly negative to +2 strongly positive, 0 neutral.
promotion.is_promotion is true when the text urges people to trade a stock for gain, sells access to tips, or predicts circuits or targets.
novelty: new_information if the text reports something not previously public, repeat if it restates a known announcement, recap for market summaries.
evidence: copy at most 240 characters from the text that justify event_type; never invent text.
You do not know what happened after the text was published; never use outside knowledge of later prices or events."""

USER_TEMPLATE = """Source: {source}
Published: {published_at} (precision: {precision})
Candidate symbols found by rules: {rule_symbols}
Allowed symbols (symbol: company): {symbol_list}

Title: {title}
Text: {body}"""

LABEL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.text_labels (
    id              BIGSERIAL PRIMARY KEY,
    item_id         BIGINT NOT NULL REFERENCES {schema}.text_items (id),
    spec_version    VARCHAR(30) NOT NULL,
    labeler         VARCHAR(80) NOT NULL,
    model_id        VARCHAR(120) NOT NULL,
    prompt_sha256   CHAR(64) NOT NULL,
    labels          JSONB NOT NULL,
    valid           BOOLEAN NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        NUMERIC(10, 6),
    labeled_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (item_id, spec_version, labeler, model_id, prompt_sha256)
);
DROP TRIGGER IF EXISTS text_labels_immutable ON {schema}.text_labels;
CREATE TRIGGER text_labels_immutable BEFORE UPDATE OR DELETE ON {schema}.text_labels
    FOR EACH ROW EXECUTE FUNCTION {schema}.text_items_reject_change();
"""

MAX_BODY_CHARS = 4000


def prompt_sha256() -> str:
    return hashlib.sha256(json.dumps({"system": SYSTEM_PROMPT, "user": USER_TEMPLATE, "schema": LABEL_SCHEMA}, sort_keys=True).encode()).hexdigest()


def build_prompt(item: dict[str, Any], symbols: dict[str, str]) -> dict[str, str]:
    if item["source"] in SOURCES_BLOCKED:
        raise PermissionError(SOURCES_BLOCKED[item["source"]])
    return {
        "system": SYSTEM_PROMPT,
        "user": USER_TEMPLATE.format(
            source=item["source"], published_at=item.get("published_at"), precision=item.get("published_precision"),
            rule_symbols=", ".join(item.get("symbols") or []) or "none",
            symbol_list="; ".join(f"{k}: {v}" for k, v in sorted(symbols.items())),
            title=item.get("title") or "", body=(item.get("body") or "")[:MAX_BODY_CHARS],
        ),
    }


def validate(labels: Any, allowed_symbols: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(labels, dict):
        return ["not an object"]
    missing = [k for k in LABEL_SCHEMA["required"] if k not in labels]
    errors += [f"missing {k}" for k in missing]
    extra = set(labels) - set(LABEL_SCHEMA["properties"])
    errors += [f"unexpected {k}" for k in sorted(extra)]
    if labels.get("event_type") not in EVENT_TYPES:
        errors.append("bad event_type")
    for entry in labels.get("symbols") or []:
        if not isinstance(entry, dict) or entry.get("symbol") not in allowed_symbols:
            errors.append(f"unknown symbol {entry.get('symbol') if isinstance(entry, dict) else entry}")
        elif entry.get("role") not in ("subject", "mentioned") or not 0 <= float(entry.get("confidence", -1)) <= 1:
            errors.append(f"bad symbol entry {entry.get('symbol')}")
    sentiment = labels.get("sentiment") or {}
    if not isinstance(sentiment.get("direction"), int) or not -2 <= sentiment["direction"] <= 2:
        errors.append("bad sentiment.direction")
    promotion = labels.get("promotion") or {}
    if not isinstance(promotion.get("is_promotion"), bool) or any(s not in PUMP_SIGNALS for s in promotion.get("signals") or []):
        errors.append("bad promotion")
    if len(str(labels.get("evidence", ""))) > 240:
        errors.append("evidence too long")
    return errors


def knowledge_time(first_seen_at: Any, published_at: Any, labeled_at: Any) -> Any:
    candidates = [t for t in (first_seen_at, published_at, labeled_at) if t is not None]
    return max(candidates)
