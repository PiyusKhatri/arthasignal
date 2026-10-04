#!/usr/bin/env bash
set -euo pipefail

DUMP_DIR="${1:?usage: restore_server.sh /path/to/dump_dir}"
DB_NAME="${DB_NAME:-arthasignal}"
APP_ROLE="${APP_ROLE:-arthasignal}"
APP_DIR="${APP_DIR:-/srv/arthasignal/app}"
PSQL="${PSQL:-sudo -u postgres psql -X -v ON_ERROR_STOP=1}"
PG_RESTORE="${PG_RESTORE:-sudo -u postgres pg_restore}"
SECRETS="${SECRETS:-/etc/arthasignal/db_passwords.env}"

cd "$DUMP_DIR"
echo "1/6 Checking the dump checksum"
shasum -a 256 -c SHA256SUMS 2>/dev/null || sha256sum -c SHA256SUMS

echo "2/6 Loading role passwords from $SECRETS"
set -a
. "$SECRETS"
set +a
: "${APP_DB_PASSWORD:?}" "${RESEARCH_DB_PASSWORD:?}" "${API_READONLY_DB_PASSWORD:?}"
SOURCE_OWNER="$(cat source_owner.txt)"

echo "3/6 Creating roles and an empty database"
$PSQL <<SQL
do \$\$ begin
  if not exists (select 1 from pg_roles where rolname = '${APP_ROLE}') then
    create role ${APP_ROLE} login nosuperuser nobypassrls createdb password '${APP_DB_PASSWORD}';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'arthasignal_research') then
    create role arthasignal_research login nosuperuser nobypassrls password '${RESEARCH_DB_PASSWORD}';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'api_readonly') then
    create role api_readonly login nosuperuser nobypassrls password '${API_READONLY_DB_PASSWORD}';
  end if;
  if not exists (select 1 from pg_roles where rolname = '${SOURCE_OWNER}') then
    create role "${SOURCE_OWNER}" nologin;
  end if;
end \$\$;
SQL
if $PSQL -Atc "select 1 from pg_database where datname = '${DB_NAME}'" | grep -q 1; then
  echo "Database ${DB_NAME} already exists. Refusing to overwrite it. Drop it yourself first if you really mean to." >&2
  exit 1
fi
$PSQL -c "create database ${DB_NAME} owner ${APP_ROLE}"
cut -d'|' -f1 extensions.txt | grep -v '^plpgsql$' | while read -r ext; do
  [ -n "$ext" ] && $PSQL -d "$DB_NAME" -c "create extension if not exists \"$ext\""
done

echo "4/6 Restoring as the postgres superuser (needed for row-level-security tables and event triggers), then handing ownership to ${APP_ROLE}"
$PG_RESTORE --dbname "$DB_NAME" --jobs 2 --no-owner --verbose "$DUMP_DIR/${DB_NAME}.dump" 2> restore.log || true
grep -i "error" restore.log | grep -v "already exists" > restore_errors.txt || true
echo "pg_restore errors (excluding 'already exists'): $(wc -l < restore_errors.txt)"
[ -s restore_errors.txt ] && head -20 restore_errors.txt
{ echo "set arthasignal.new_owner = '${APP_ROLE}';"; cat "$APP_DIR/deploy/sql/reassign_owner.sql"; } | $PSQL -d "$DB_NAME"
{ echo "set arthasignal.new_owner = '${APP_ROLE}';"; cat "$APP_DIR/deploy/sql/role_grants.sql"; } | $PSQL -d "$DB_NAME"
$PSQL -d "$DB_NAME" -c "grant connect on database ${DB_NAME} to arthasignal_research, api_readonly"

echo "5/6 Re-applying the holdout guard (row-level security for arthasignal_research)"
cd "$APP_DIR"
sudo -u arthasignal --preserve-env=DATABASE_URL bash -c "set -a; . $APP_DIR/.env; set +a; .venv/bin/python -m src.database.holdout_guard"
sudo -u arthasignal bash -c "set -a; . $APP_DIR/.env; set +a; .venv/bin/python -m src.scorecard.schema >/dev/null"

echo "6/6 Comparing row counts with the dump manifest"
cd "$DUMP_DIR"
$PSQL -d "$DB_NAME" -At > row_counts_server.txt <<'SQL'
select format('select %L || ''|'' || count(*) from %I.%I;', n.nspname || '.' || c.relname, n.nspname, c.relname)
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r', 'p') and n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_toast'
order by 1
\gexec
SQL
if diff <(sort row_counts.txt) <(sort row_counts_server.txt) > row_count_diff.txt; then
  echo "Row counts match for all $(wc -l < row_counts.txt) tables."
else
  echo "ROW COUNT MISMATCH, see $DUMP_DIR/row_count_diff.txt:" >&2
  head -40 row_count_diff.txt >&2
  exit 2
fi
