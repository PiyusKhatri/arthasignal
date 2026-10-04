#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash deploy/scripts/bootstrap_server.sh" >&2
  exit 1
fi
REPO_URL="${REPO_URL:?set REPO_URL, e.g. git@github.com:PiyusKhatri/arthasignal.git}"
APP_HOME=/srv/arthasignal
APP_DIR=$APP_HOME/app

echo "== 1. Time zone, packages, swap"
timedatectl set-timezone Asia/Kathmandu
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get -y install git curl rsync build-essential libgomp1 age ufw unattended-upgrades logrotate postgresql-common ca-certificates
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== 2. PostgreSQL 17 (PGDG), listening on localhost only"
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
DEBIAN_FRONTEND=noninteractive apt-get -y install postgresql-17
install -m 644 "$(dirname "$0")/../config/postgresql-arthasignal.conf" /etc/postgresql/17/main/conf.d/arthasignal.conf
systemctl restart postgresql
ss -ltnp | grep 5432

echo "== 3. Firewall: SSH only"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
ufw status verbose

echo "== 4. Service user and directories"
id arthasignal >/dev/null 2>&1 || useradd --system --create-home --home-dir $APP_HOME --shell /bin/bash arthasignal
usermod -aG systemd-journal arthasignal
install -d -m 750 -o arthasignal -g arthasignal $APP_HOME
install -d -m 750 -o root -g arthasignal /etc/arthasignal
install -d -m 750 -o root -g arthasignal /var/backups/arthasignal

echo "== 5. Code, uv and Python 3.12"
if [ ! -d $APP_DIR/.git ]; then
  sudo -u arthasignal git clone "$REPO_URL" $APP_DIR
fi
sudo -u arthasignal bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u arthasignal bash -lc "cd $APP_DIR && ~/.local/bin/uv python install 3.12 && ~/.local/bin/uv venv --python 3.12 .venv && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt"
sudo -u arthasignal bash -lc "cd $APP_DIR && .venv/bin/python --version"

echo "== 6. Logs, journald limits, log rotation"
sudo -u arthasignal mkdir -p $APP_DIR/logs
install -m 644 $APP_DIR/deploy/config/logrotate-arthasignal /etc/logrotate.d/arthasignal
install -m 644 $APP_DIR/deploy/config/tmpfiles-arthasignal.conf /etc/tmpfiles.d/arthasignal.conf
install -d /etc/systemd/journald.conf.d
install -m 644 $APP_DIR/deploy/config/journald-arthasignal.conf /etc/systemd/journald.conf.d/arthasignal.conf
systemctl restart systemd-journald
logrotate --debug /etc/logrotate.d/arthasignal >/dev/null 2>&1 && echo "logrotate config OK"

echo "== 7. Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Bootstrap finished. Next: create secrets (.env, db passwords, backup key), restore the dump, install the systemd units."
