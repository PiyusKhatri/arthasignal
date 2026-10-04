#!/usr/bin/env bash
set -euo pipefail
ROOT="${ARTHASIGNAL_ROOT:-/srv/arthasignal}"
cd "$ROOT"
mkdir -p logs/league
STAMP="$(TZ=Asia/Kathmandu date +%Y-%m-%dT%H:%M:%S%z)"
exec 9>"$ROOT/logs/league/.lock"
if ! flock -n 9; then
  echo "$STAMP another league run is active" >> logs/league/league.log
  exit 4
fi
set +e
"$ROOT/.venv/bin/python" -m src.league.run --markdown-dir logs/league "$@" > "logs/league/run_$(TZ=Asia/Kathmandu date +%Y%m%d_%H%M%S).json" 2>> logs/league/league.log
STATUS=$?
set -e
echo "$STAMP exit $STATUS" >> logs/league/league.log
exit $STATUS
