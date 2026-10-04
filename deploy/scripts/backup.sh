#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-arthasignal}"
BACKUP_DIR="${ARTHASIGNAL_BACKUP_DIR:-/var/backups/arthasignal}"
RECIPIENT_FILE="${RECIPIENT_FILE:-/etc/arthasignal/backup_recipient.txt}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(TZ=Asia/Kathmandu date +%Y%m%d_%H%M%S)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

install -d -m 750 -o root -g arthasignal "$BACKUP_DIR"
runuser -u postgres -- pg_dump --format=custom --compress=6 "$DB_NAME" > "$TMP/db.dump"
runuser -u postgres -- pg_dumpall --globals-only > "$TMP/globals.sql"
tar -C "$TMP" -cf - db.dump globals.sql | age -R "$RECIPIENT_FILE" > "$BACKUP_DIR/arthasignal_${STAMP}.dump.age.part"
mv "$BACKUP_DIR/arthasignal_${STAMP}.dump.age.part" "$BACKUP_DIR/arthasignal_${STAMP}.dump.age"
chmod 640 "$BACKUP_DIR/arthasignal_${STAMP}.dump.age"
chgrp arthasignal "$BACKUP_DIR/arthasignal_${STAMP}.dump.age"
find "$BACKUP_DIR" -name 'arthasignal_*.dump.age' -mtime +"$KEEP_DAYS" -delete

if [ -n "${BACKUP_S3_URI:-}" ]; then
  aws s3 cp --only-show-errors "$BACKUP_DIR/arthasignal_${STAMP}.dump.age" "${BACKUP_S3_URI%/}/arthasignal_${STAMP}.dump.age"
fi
echo "backup written: $BACKUP_DIR/arthasignal_${STAMP}.dump.age ($(du -h "$BACKUP_DIR/arthasignal_${STAMP}.dump.age" | cut -f1))"
