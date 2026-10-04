#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo" >&2
  exit 1
fi
APP_DIR=/srv/arthasignal/app
install -m 644 $APP_DIR/deploy/systemd/*.service $APP_DIR/deploy/systemd/*.timer /etc/systemd/system/
chmod 755 $APP_DIR/deploy/scripts/*.sh
systemctl daemon-reload
for unit in /etc/systemd/system/arthasignal-*.service; do
  systemd-analyze verify "$unit" || true
done
for timer in daily news tips health weekly purge backup; do
  systemctl enable --now "arthasignal-$timer.timer"
done
systemctl list-timers 'arthasignal-*' --all
