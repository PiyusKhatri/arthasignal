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
    database_url_readonly: str
    discord_webhook_url: str | None
    google_service_account_json: str | None
    google_drive_folder_id: str | None
    jwt_secret_key: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from_email: str | None
    smtp_use_tls: bool
    frontend_base_url: str


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error("Missing required environment variable: %s", name)
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    database_url = _require_env("DATABASE_URL")
    return Settings(
        database_url=database_url,
        # A dedicated read replica is optional. Local development and small
        # deployments intentionally share the primary DB unless configured.
        database_url_readonly=os.getenv("DATABASE_URL_READONLY") or database_url,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
        google_drive_folder_id=os.getenv("GOOGLE_DRIVE_FOLDER_ID"),
        # JWT is required only by auth operations, not by ingestion pipelines.
        jwt_secret_key=os.getenv("JWT_SECRET_KEY"),
        smtp_host=os.getenv("SMTP_HOST"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME"),
        smtp_password=os.getenv("SMTP_PASSWORD"),
        smtp_from_email=os.getenv("SMTP_FROM_EMAIL"),
        smtp_use_tls=_env_bool("SMTP_USE_TLS", True),
        frontend_base_url=os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/"),
    )


settings = get_settings()
