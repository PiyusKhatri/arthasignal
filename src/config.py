from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    discord_webhook_url: str | None
    google_service_account_json: str | None
    google_drive_folder_id: str | None


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error("Missing required environment variable: %s", name)
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def get_settings() -> Settings:
    return Settings(
        database_url=_require_env("DATABASE_URL"),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
        google_drive_folder_id=os.getenv("GOOGLE_DRIVE_FOLDER_ID"),
    )


settings = get_settings()
