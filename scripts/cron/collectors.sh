#!/usr/bin/env bash
set -euo pipefail
ROOT="${ARTHASIGNAL_ROOT:-/srv/arthasignal}"
TARGET="${1:?usage: collectors.sh news|social|purge|<source>}"
shift
cd "$ROOT"
mkdir -p logs/collectors
if [ -f "$ROOT/.env.collectors" ]; then
  set -a
  . "$ROOT/.env.collectors"
  set +a
fi
STAMP="$(TZ=Asia/Kathmandu date +%Y-%m-%dT%H:%M:%S%z)"
exec 9>"$ROOT/logs/collectors/.lock-$TARGET"
if ! flock -n 9; then
  echo "$STAMP $TARGET another run is active" >> logs/collectors/collectors.log
  exit 4
fi
set +e
"$ROOT/.venv/bin/python" -m src.collectors.run "$TARGET" "$@" > "logs/collectors/${TARGET}_$(TZ=Asia/Kathmandu date +%Y%m%d_%H%M%S).json" 2>> logs/collectors/collectors.log
STATUS=$?
set -e
echo "$STAMP $TARGET exit $STATUS" >> logs/collectors/collectors.log
exit $STATUS
