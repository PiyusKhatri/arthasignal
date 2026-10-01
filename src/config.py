from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


BLOCKED_DATABASE_HOST_SUFFIXES = ("supabase.co", "supabase.com")


def assert_allowed_database_url(name: str, url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if any(host == suffix or host.endswith("." + suffix) for suffix in BLOCKED_DATABASE_HOST_SUFFIXES):
        raise ConfigError(f"{name} points at a Supabase host ({host}); only the local Postgres database is allowed")
    return url


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
    mistral_api_key: str | None


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
    database_url = assert_allowed_database_url("DATABASE_URL", _require_env("DATABASE_URL"))
    database_url_readonly = assert_allowed_database_url(
        "DATABASE_URL_READONLY", os.getenv("DATABASE_URL_READONLY") or database_url
    )
    return Settings(
        database_url=database_url,
        # A dedicated read replica is optional. Local development and small
        # deployments intentionally share the primary DB unless configured.
        database_url_readonly=database_url_readonly,
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
        mistral_api_key=os.getenv("MISTRAL_API_KEY"),
    )


settings = get_settings()
