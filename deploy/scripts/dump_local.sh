#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${DB_NAME:-arthasignal}"
OUT_ROOT="${1:-$HOME/arthasignal_dump}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$OUT_ROOT/$STAMP"
PG_BIN="${PG_BIN:-$(dirname "$(command -v pg_dump)")}"

mkdir -p "$OUT"
echo "Dumping database $DB_NAME into $OUT"
"$PG_BIN/pg_dump" --version | tee "$OUT/pg_dump_version.txt"
"$PG_BIN/psql" -X -d "$DB_NAME" -Atc "select version()" > "$OUT/server_version.txt"
"$PG_BIN/psql" -X -d "$DB_NAME" -Atc "select current_user" > "$OUT/source_owner.txt"

"$PG_BIN/pg_dump" -d "$DB_NAME" --format=custom --compress=6 --verbose --file "$OUT/$DB_NAME.dump" 2> "$OUT/pg_dump.log"

"$PG_BIN/psql" -X -d "$DB_NAME" -At > "$OUT/roles.txt" <<'SQL'
select rolname || '|' || rolsuper || '|' || rolbypassrls || '|' || rolcanlogin
from pg_roles where rolname !~ '^pg_' order by 1;
SQL

"$PG_BIN/psql" -X -d "$DB_NAME" -At > "$OUT/extensions.txt" <<'SQL'
select extname || '|' || extversion from pg_extension order by 1;
SQL

"$PG_BIN/psql" -X -d "$DB_NAME" -At > "$OUT/row_counts.txt" <<'SQL'
select format('select %L || ''|'' || count(*) from %I.%I;', n.nspname || '.' || c.relname, n.nspname, c.relname)
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r', 'p') and n.nspname not in ('pg_catalog', 'information_schema') and n.nspname !~ '^pg_toast'
order by 1
\gexec
SQL

( cd "$OUT" && shasum -a 256 "$DB_NAME.dump" > SHA256SUMS )
echo
echo "Done. Files:"
ls -lh "$OUT"
echo
echo "Tables counted: $(wc -l < "$OUT/row_counts.txt")"
echo "Copy to the server with:"
echo "  rsync -avP $OUT ubuntu@<ELASTIC_IP>:/tmp/arthasignal_dump/"
