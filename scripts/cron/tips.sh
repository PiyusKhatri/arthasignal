#!/usr/bin/env bash
set -euo pipefail
ROOT="${ARTHASIGNAL_ROOT:-/srv/arthasignal}"
TARGET="${1:?usage: tips.sh cycle [--grade]}"
shift
cd "$ROOT"
mkdir -p logs/tips
if [ -f "$ROOT/.env.collectors" ]; then
  set -a
  . "$ROOT/.env.collectors"
  set +a
fi
STAMP="$(TZ=Asia/Kathmandu date +%Y-%m-%dT%H:%M:%S%z)"
exec 9>"$ROOT/logs/tips/.lock-$TARGET"
if ! flock -n 9; then
  echo "$STAMP $TARGET another run is active" >> logs/tips/tips.log
  exit 4
fi
set +e
"$ROOT/.venv/bin/python" -m src.tips.run "$TARGET" "$@" > "logs/tips/${TARGET}_$(TZ=Asia/Kathmandu date +%Y%m%d_%H%M%S).json" 2>> logs/tips/tips.log
STATUS=$?
set -e
echo "$STAMP $TARGET exit $STATUS" >> logs/tips/tips.log
exit $STATUS
