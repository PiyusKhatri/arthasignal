from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from src.config import settings

logger = logging.getLogger(__name__)

DISCORD_TIMEOUT_SECONDS = 15
COLOR_FAILURE = 0xE74C3C
COLOR_SUCCESS = 0x2ECC71


def send_discord_alert(message: str, is_failure: bool) -> bool:
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not configured, skipping alert")
        return False

    embed = {
        "title": "Pipeline Failure" if is_failure else "Pipeline Success",
        "description": message,
        "color": COLOR_FAILURE if is_failure else COLOR_SUCCESS,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        response = requests.post(
            settings.discord_webhook_url,
            json={"embeds": [embed]},
            timeout=DISCORD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except Exception:
        logger.exception("Failed to send Discord alert")
        return False
