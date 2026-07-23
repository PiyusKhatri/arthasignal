from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from src.config import settings

logger = logging.getLogger(__name__)

DISCORD_TIMEOUT_SECONDS = 15
COLOR_FAILURE = 0xE74C3C
COLOR_WARNING = 0xF39C12
COLOR_SUCCESS = 0x2ECC71

SEVERITY_TITLES = {
    "success": "Pipeline Success",
    "warning": "Pipeline Completed with Warnings",
    "failure": "Pipeline Failure",
}
SEVERITY_COLORS = {
    "success": COLOR_SUCCESS,
    "warning": COLOR_WARNING,
    "failure": COLOR_FAILURE,
}


def send_discord_alert(message: str, severity: str) -> bool:
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not configured, skipping alert")
        return False

    embed = {
        "title": SEVERITY_TITLES[severity],
        "description": message,
        "color": SEVERITY_COLORS[severity],
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
