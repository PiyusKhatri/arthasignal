from __future__ import annotations

import glob
import logging
import os
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
BACKUP_FILE_PREFIX = "arthasignal_backup_"
RETENTION_DAYS = 30


def _write_credentials_temp_file() -> str:
    if not settings.google_service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write(settings.google_service_account_json)
    return path


def _get_drive_service():
    creds_path = _write_credentials_temp_file()
    try:
        credentials = service_account.Credentials.from_service_account_file(creds_path, scopes=DRIVE_SCOPES)
        return build("drive", "v3", credentials=credentials)
    finally:
        os.remove(creds_path)


def _resolve_pg_dump_binary() -> str:
    override = os.getenv("PG_DUMP_BINARY")
    if override:
        logger.info("PG_DUMP_RESOLUTION: using PG_DUMP_BINARY override=%s", override)
        return override

    raw_matches = glob.glob("/usr/lib/postgresql/*/bin/pg_dump")
    logger.info("PG_DUMP_RESOLUTION: glob /usr/lib/postgresql/*/bin/pg_dump found=%r", raw_matches)

    candidates = sorted(raw_matches, key=lambda path: int(path.split("/")[4]), reverse=True)
    if candidates:
        chosen = candidates[0]
        logger.info(
            "PG_DUMP_RESOLUTION: chosen=%s (sorted candidates=%r, bypassing the ambiguous pg_wrapper "
            "version-dispatch script, which can silently pick an older client version depending on "
            "local cluster/config state)",
            chosen,
            candidates,
        )
        return chosen

    logger.warning(
        "PG_DUMP_RESOLUTION: no versioned pg_dump binary found under /usr/lib/postgresql/*/bin, "
        "falling back to PATH resolution (this will go through the ambiguous pg_wrapper script)"
    )
    return "pg_dump"


def _dump_database() -> str:
    filename = f"{BACKUP_FILE_PREFIX}{date.today().isoformat()}.sql"
    output_path = os.path.join(tempfile.gettempdir(), filename)
    pg_dump_binary = _resolve_pg_dump_binary()
    logger.info("PG_DUMP_RESOLUTION: subprocess will invoke binary_path=%s", pg_dump_binary)

    version_result = subprocess.run([pg_dump_binary, "--version"], capture_output=True, text=True)
    logger.info(
        "PG_DUMP_RESOLUTION: %s --version -> %s",
        pg_dump_binary,
        version_result.stdout.strip() or version_result.stderr.strip(),
    )

    result = subprocess.run(
        [pg_dump_binary, f"--dbname={settings.database_url}", "-f", output_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr}")
    logger.info("Database dumped to %s using %s", output_path, pg_dump_binary)
    return output_path


def _upload_file(drive_service, local_path: str) -> str:
    if not settings.google_drive_folder_id:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID is not configured")

    file_metadata = {
        "name": os.path.basename(local_path),
        "parents": [settings.google_drive_folder_id],
    }
    media = MediaFileUpload(local_path, mimetype="application/sql", resumable=True)
    uploaded = (
        drive_service.files()
        .create(body=file_metadata, media_body=media, fields="id, name", supportsAllDrives=True)
        .execute()
    )
    logger.info("Uploaded %s to Drive with file id %s", uploaded["name"], uploaded["id"])
    return uploaded["id"]


def _delete_old_backups(drive_service) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    query = f"'{settings.google_drive_folder_id}' in parents and trashed = false and name contains '{BACKUP_FILE_PREFIX}'"
    response = (
        drive_service.files()
        .list(
            q=query,
            corpora="drive",
            driveId=settings.google_drive_folder_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="files(id, name, createdTime)",
        )
        .execute()
    )
    files = response.get("files", [])

    deleted_count = 0
    for file in files:
        created_time = datetime.fromisoformat(file["createdTime"].replace("Z", "+00:00"))
        if created_time < cutoff:
            drive_service.files().delete(fileId=file["id"], supportsAllDrives=True).execute()
            logger.info("Deleted old backup %s (created %s)", file["name"], file["createdTime"])
            deleted_count += 1

    return deleted_count


def run_backup() -> dict[str, Any]:
    start_time = time.perf_counter()

    local_path = _dump_database()
    try:
        drive_service = _get_drive_service()
        drive_file_id = _upload_file(drive_service, local_path)
        deleted_count = _delete_old_backups(drive_service)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

    elapsed_seconds = time.perf_counter() - start_time

    summary = {
        "backup_filename": os.path.basename(local_path),
        "drive_file_id": drive_file_id,
        "old_backups_deleted": deleted_count,
        "execution_time_seconds": round(elapsed_seconds, 2),
    }

    logger.info(
        "Backup summary: backup_filename=%s drive_file_id=%s old_backups_deleted=%d execution_time_seconds=%.2f",
        summary["backup_filename"],
        summary["drive_file_id"],
        summary["old_backups_deleted"],
        summary["execution_time_seconds"],
    )

    return summary


if __name__ == "__main__":
    run_backup()
