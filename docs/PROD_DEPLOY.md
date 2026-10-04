# Production deployment: one Ubuntu 24.04 server in AWS Mumbai

This settles the "PRODUCTION DATABASE TOPOLOGY" item in `TODOS.md`. The database and every scheduled job run on one server, and nothing reaches Postgres from outside. It replaces the GitHub-hosted runner idea in the README for the prediction system. The website and API are **not** deployed by this kit.

Everything below is typed by you. Nothing in this repository connects to the server by itself.

**What runs on the server (all times Nepal time; the server clock is set to Asia/Kathmandu):**

| When | Job (systemd unit) | What it does |
| --- | --- | --- |
| Mon-Fri 15:35 | `arthasignal-daily` | The post-close chain; order and failure rules are below. It stops early with "no session" on a holiday |
| Every 30 min | `arthasignal-news` | News collectors |
| Hourly at :05, and Mon-Fri 10:25 | `arthasignal-tips` | Tip collection and ledger |
| Hourly at :50 | `arthasignal-health` | Discord alert for a failed job, stale prices, writers not done by 21:30, stale news, stale backup, or disk above 85% |
| Daily 02:30 | `arthasignal-backup` | Encrypted database backup (`age`), kept 14 days |
| Daily 03:30 | `arthasignal-purge` | Deletes YouTube and Reddit text after 30 days |
| Saturday 09:00 | `arthasignal-weekly` | The weekly report to Discord |
| On any failure | `arthasignal-notify@` | Posts the failed unit and its last log lines to Discord |

**The post-close chain, in order** (`python -m src.ops.daily --list` prints the same list on the server). Since 2026-10-04 the calls are written **before** anything that cannot make them invalid:

| # | Step | If it fails | Why |
| ---: | --- | --- | --- |
| 1 | capture: today's prices and NEPSE index (NEPSE API, then Sharesansar, then Merolagani; retries every 15 min until 20:30) | **Hard stop**: every later step except news is skipped; Discord alert | Every call is computed from today's prices and index. Without them no call is valid |
| 2 | integrity: unresolved price steps in the last 60 sessions | Three outcomes, described below | Only bad prices for a symbol make that symbol's call invalid |
| 3 | league: the six paper bots (b2) | Recorded; the other writers still run; alert | Writers are independent of each other |
| 4 | avoid_writer: model v0.1 calls and avoid observations | Same | Same |
| 5 | tips: public tips to the ledger | Same | Same |
| 6 | sectors: no non-equity symbol has an equity sector | **Never blocks**; alert | Calls use only the Equity price panel, so non-equity labels cannot make a call invalid |
| 7 | quarterly_capture | Never blocks | No call uses it today; it only accrues data for later |
| 8 | news collectors | Never blocks; also runs every 30 min on its own | No call uses it |
| 9 | grading of matured calls | Never blocks; retried the next day | Grading never changes a written call |
| 10 | metrics: leaderboards | Never blocks | Reporting only |
| 11 | daily report to Discord | Always runs | |

The three integrity outcomes:
- **exit 3, specific symbols flagged:** they are written to `logs/daily/<date>/exclude_symbols.json`, the writers **drop** those symbols from today's calls and write the rest, and Discord gets a warning. Dropped symbols are not replaced by the next-ranked stock, because the frozen bot rules do not include a replacement; a bot may make fewer calls that day.
- **exit 4, 10% or more of traded equities flagged:** that indicates a feed-wide price problem, so the writers are skipped and Discord gets an alert. Fix the data, then before the next open run `sudo systemctl start arthasignal-daily-resume.service`.
- **The checker itself crashes:** the writers still run and Discord is alerted. A crash says nothing about the data, and the grader already excludes any holding window that spans an unresolved step, so an unflagged bad symbol cannot inflate results; it only wastes a call slot that day.

**The deadline (protocol v2.1):** calls for a session must be written before the **next session opens** (11:00). If the evening chain fails, you have until then to fix it and run `sudo systemctl start arthasignal-daily-resume.service`.

---

## Part 1: Things to prepare on your laptop (about 20 minutes)

1. **AWS account** with permission to create EC2 instances.
2. **Discord webhook:** in your Discord server, open the channel → ⚙ Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL. Keep it private; it goes only into the server's `.env`.
3. **Backup encryption key.** The server can encrypt backups but cannot read them. On your Mac:
   ```bash
   brew install age
   age-keygen -o ~/arthasignal-backup-key.txt
   ```
   It prints a line starting `Public key: age1…`. Copy that `age1…` text; you need it in Part 5. Keep `~/arthasignal-backup-key.txt` safe, in two places such as a password manager and a USB stick. **Without it, backups cannot be restored.**
4. **Dump the local database** (excluding nothing). From the repository folder:
   ```bash
   cd ~/Desktop/arthasignal
   bash deploy/scripts/dump_local.sh
   ```
   It writes `~/arthasignal_dump/<timestamp>/` containing:
   - `arthasignal.dump` and `SHA256SUMS`;
   - `row_counts.txt`, one line per table, which the restore checks against;
   - `roles.txt` and `extensions.txt`.

   On 2026-10-04 it took 33 seconds and produced a 286 MB dump covering 113 tables (see the check at the end of this document).

## Part 2: Create the server in AWS (about 15 minutes)

1. Sign in to the AWS console. In the top-right region menu, choose **Asia Pacific (Mumbai) ap-south-1**.
2. Go to **EC2 → Instances → Launch instances**:
   - **Name:** `arthasignal-prod`;
   - **Application and OS image:** Ubuntu → **Ubuntu Server 24.04 LTS**, architecture **64-bit (x86)**;
   - **Instance type:** `t3.medium`;
   - **Key pair:** Create new key pair → name `arthasignal-prod`, type ED25519, format `.pem` → it downloads. On your Mac, move it and protect it:
     ```bash
     mkdir -p ~/.ssh && mv ~/Downloads/arthasignal-prod.pem ~/.ssh/ && chmod 400 ~/.ssh/arthasignal-prod.pem
     ```
   - **Network settings → Edit:** create a security group named `arthasignal-ssh-only` with **one** inbound rule: type SSH, port 22, source **My IP**. Do **not** add any rule for port 5432 or for HTTP;
   - **Configure storage:** 60 GiB, `gp3`;
   - Click **Launch instance**.
3. **Elastic IP:** EC2 → Network & Security → **Elastic IPs** → Allocate Elastic IP address → Allocate. Then select it → Actions → **Associate Elastic IP address** → choose the `arthasignal-prod` instance → Associate. Write the IP down; below it is `<ELASTIC_IP>`.
4. Connect from your Mac (the first time, answer `yes`):
   ```bash
   ssh -i ~/.ssh/arthasignal-prod.pem ubuntu@<ELASTIC_IP>
   ```
   If your home IP changes later and SSH stops working, edit the security group's SSH rule to your new IP.

## Part 3: Get the code onto the server (about 10 minutes)

Everything in Parts 3-9 is typed **on the server** (after `ssh …`), unless it says "on your Mac".

1. Create the service user and a read-only deploy key for GitHub:
   ```bash
   sudo useradd --system --create-home --home-dir /srv/arthasignal --shell /bin/bash arthasignal
   sudo -u arthasignal ssh-keygen -t ed25519 -N "" -f /srv/arthasignal/.ssh/id_ed25519
   sudo cat /srv/arthasignal/.ssh/id_ed25519.pub
   ```
2. On GitHub, open the repository → Settings → **Deploy keys** → Add deploy key. Title `arthasignal-prod`, paste the line printed above, and leave **Allow write access unticked**. Save.
3. Back on the server:
   ```bash
   sudo -u arthasignal ssh -o StrictHostKeyChecking=accept-new -T git@github.com
   sudo -u arthasignal git clone git@github.com:PiyusKhatri/arthasignal.git /srv/arthasignal/app
   ```
   The first command should print "You've successfully authenticated".

## Part 4: Install everything (about 15 minutes, one command)

```bash
sudo REPO_URL=git@github.com:PiyusKhatri/arthasignal.git bash /srv/arthasignal/app/deploy/scripts/bootstrap_server.sh
```

What it does:

- sets the time zone to Asia/Kathmandu;
- updates Ubuntu and adds 2 GB of swap;
- installs PostgreSQL 17 from the official PostgreSQL apt repository, **listening on localhost only**;
- enables the `ufw` firewall: **SSH only**;
- installs `uv`, Python 3.12 and the Python packages into `/srv/arthasignal/app/.venv`;
- sets up log rotation, journald limits and automatic security updates.

Check that it worked:

```bash
sudo ufw status            # Status: active, 22/tcp (OpenSSH) ALLOW
sudo ss -ltnp | grep 5432  # must show 127.0.0.1:5432 only, never 0.0.0.0
sudo -u arthasignal /srv/arthasignal/app/.venv/bin/python --version   # Python 3.12.x
```

## Part 5: Secrets (about 10 minutes)

1. **Database passwords.** Generate three random passwords:
   ```bash
   for i in 1 2 3; do openssl rand -base64 24 | tr -d '/+='; done
   ```
   Put them into the password file:
   ```bash
   sudo cp /srv/arthasignal/app/deploy/config/db_passwords.env.example /etc/arthasignal/db_passwords.env
   sudo nano /etc/arthasignal/db_passwords.env      # replace the three CHANGE_ME values
   sudo chmod 640 /etc/arthasignal/db_passwords.env && sudo chown root:arthasignal /etc/arthasignal/db_passwords.env
   ```
2. **The app's `.env`** (never committed; `.gitignore` already excludes it):
   ```bash
   sudo -u arthasignal cp /srv/arthasignal/app/deploy/config/env.production.example /srv/arthasignal/app/.env
   sudo -u arthasignal nano /srv/arthasignal/app/.env
   sudo chmod 600 /srv/arthasignal/app/.env
   ```
   Fill it in:
   - the app password in `DATABASE_URL`;
   - the api_readonly password in `DATABASE_URL_READONLY`;
   - your Discord webhook in `DISCORD_WEBHOOK_URL`;
   - a new random `JWT_SECRET_KEY` (`openssl rand -hex 32`).
3. **Research role login.** The holdout guard (`src/database/holdout_guard.py`, `research_url()`) builds its connection from `DATABASE_URL`: same host, port and database, user `arthasignal_research`, and **no password at all**. The app's password is never reused for it. The login library therefore reads the research password from `.pgpass`. Use the RESEARCH password from step 1:
   ```bash
   echo "localhost:5432:arthasignal:arthasignal_research:<RESEARCH_PASSWORD>" | sudo -u arthasignal tee /srv/arthasignal/.pgpass >/dev/null
   sudo chmod 600 /srv/arthasignal/.pgpass
   ```
   - The host field must match the host written in `DATABASE_URL` exactly. With the template that is `localhost`; if you wrote `127.0.0.1` there, write `127.0.0.1` here.
   - The file must be `chmod 600`, or it is ignored.
   - **Optional alternative:** instead of `.pgpass`, put `RESEARCH_DATABASE_URL=postgresql+psycopg2://arthasignal_research:<RESEARCH_PASSWORD>@localhost:5432/arthasignal` in `.env`. It is refused unless the user name is exactly `arthasignal_research`, so it can never point the guard at the app role.
   - Before 2026-10-04 the code reused the app password for this login, which fails on a server with real passwords. If your server checkout is older than that, `git pull` first.
4. **Backup public key** (the `age1…` line from Part 1, step 3):
   ```bash
   echo "age1PASTE_YOUR_PUBLIC_KEY" | sudo tee /etc/arthasignal/backup_recipient.txt
   ```
   Optional off-server copies to S3: create an S3 bucket in ap-south-1, give the instance an IAM role allowed to `s3:PutObject` on it, install the AWS CLI (`sudo snap install aws-cli --classic`), and put `BACKUP_S3_URI=s3://your-bucket/arthasignal` in `/etc/arthasignal/backup.env`.

## Part 6: Copy the database and floorsheet files (time depends on upload speed)

On your Mac, using the folder name printed by `dump_local.sh`:

```bash
rsync -avP -e "ssh -i ~/.ssh/arthasignal-prod.pem" ~/arthasignal_dump/<timestamp> ubuntu@<ELASTIC_IP>:/tmp/arthasignal_dump/
rsync -avP -e "ssh -i ~/.ssh/arthasignal-prod.pem" ~/Desktop/arthasignal-ai/raw/floorsheet/ ubuntu@<ELASTIC_IP>:/tmp/floorsheet/
```

On the server:

```bash
sudo bash /srv/arthasignal/app/deploy/scripts/restore_server.sh /tmp/arthasignal_dump/<timestamp>
sudo install -d -o arthasignal -g arthasignal /srv/arthasignal/data
sudo mv /tmp/floorsheet /srv/arthasignal/data/floorsheet
sudo chown -R arthasignal:arthasignal /srv/arthasignal/data/floorsheet
sudo chmod -R a-w /srv/arthasignal/data/floorsheet
```

The restore does the following:

1. checks the dump's checksum;
2. creates the roles (`arthasignal` owner, `arthasignal_research`, `api_readonly`; none of them superuser or BYPASSRLS);
3. restores everything;
4. re-applies the **holdout guard** (row-level security);
5. applies the v2.1 ledger schema;
6. compares the row count of **every table** with your laptop.

It must end with `Row counts match for all N tables.` It refuses to overwrite an existing database. The floorsheet Parquet files stay read-only.

Why the restore works this way:
- It restores as the `postgres` superuser and then hands every object to the `arthasignal` role (`deploy/sql/reassign_owner.sql`). A restore run directly as the non-superuser owner fails on the row-level-security tables and on the event triggers from the old Supabase schema; this was tested on the laptop.
- `arthasignal_research` gets explicit rights (`deploy/sql/role_grants.sql`): read everything, and insert only into the registry and ledger tables. On the laptop it instead inherits everything from the superuser owner role, which is broader than it should be.

**Check the holdout guard:**

```bash
cd /srv/arthasignal/app
sudo -u arthasignal bash -lc 'cd /srv/arthasignal/app && set -a && . ./.env && set +a && .venv/bin/python -c "
from sqlalchemy import text
from src.database.holdout_guard import research_engine
with research_engine().connect() as c:
    print(\"research role latest price date:\", c.execute(text(\"select max(date) from daily_prices\")).scalar())
"'
```

It must print `2025-09-28`, the last session before the holdout. If it prints a later date, stop and do not run research on this server.

If it fails instead:
- "fe_sendauth: no password supplied" means `.pgpass` is missing, not `chmod 600`, or its host field differs from `DATABASE_URL`'s host.
- "password authentication failed for user arthasignal_research" means the password in `.pgpass` is not the RESEARCH password from `/etc/arthasignal/db_passwords.env`.

## Part 7: Fill the missing price sessions (once)

Prices stop at 2026-09-29 on the laptop. On the server:

```bash
cd /srv/arthasignal/app
sudo -u arthasignal bash -lc 'cd /srv/arthasignal/app && set -a && . ./.env && set +a && .venv/bin/python -m src.ops.backfill --since 2026-09-29 --dry-run'
sudo -u arthasignal bash -lc 'cd /srv/arthasignal/app && set -a && . ./.env && set +a && .venv/bin/python -m src.ops.backfill --since 2026-09-29'
```

The dry run lists the sessions the calendar expects. On 2026-10-04 it expected 2026-09-30, 10-01 and 10-02, and all three were missing. The real run fetches them and prints what is still missing afterwards.

A day still missing after the backfill is either:
- a **holiday** (Dashain falls in this period). Add it to `config/nepse_calendar.json` under `"holidays"`, for example `{"date": "2026-10-01", "source": "NEPSE notice …"}`, commit the change, and `git pull` on the server; or
- a day no source could supply. Rerun later.

The NEPSE API sometimes refuses non-Nepal addresses; the Sharesansar and Merolagani fallbacks are used automatically.

## Part 8: Turn on the schedule

```bash
sudo bash /srv/arthasignal/app/deploy/scripts/install_units.sh
systemctl list-timers 'arthasignal-*'
```

You should see eight timers with their next run times.

**Test Discord now:**

```bash
sudo systemctl start arthasignal-health.service
sudo systemctl start arthasignal-notify@test-alert.service
sudo -u arthasignal bash -lc 'cd /srv/arthasignal/app && set -a && . ./.env && set +a && .venv/bin/python -m src.ops.report daily'
```

You should get:
- a health alert about stale data or a missing backup, until the first backup runs;
- a "test-alert failed" message;
- a daily report.

**Run a backup now** (instead of waiting until 02:30) and check it:

```bash
sudo systemctl start arthasignal-backup.service
sudo ls -lh /var/backups/arthasignal/
```

## Part 9: Everyday operation

| Task | Command (on the server) |
| --- | --- |
| See what ran today | `sudo journalctl -u arthasignal-daily --since today` and `ls /srv/arthasignal/app/logs/daily/$(date +%F)/` |
| Rerun from a step | `sudo -u arthasignal bash -lc 'cd /srv/arthasignal/app && set -a && . ./.env && set +a && .venv/bin/python -m src.ops.daily --from league'` (step names: `python -m src.ops.daily --list`) |
| Resume the writers before the deadline | `sudo systemctl start arthasignal-daily-resume.service` |
| A price step failed the integrity check | Read `logs/daily/<date>/integrity.json`. Either the corporate action is missing (record it) or the symbol needs a quarantine row (`price_quarantine`). Then resume the writers before 11:00 on the next session day |
| Add a NEPSE holiday | Edit `config/nepse_calendar.json` on your laptop, commit and push, then `cd /srv/arthasignal/app && sudo -u arthasignal git pull` |
| Update the code | `cd /srv/arthasignal/app && sudo -u arthasignal git pull && sudo -u arthasignal /srv/arthasignal/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt && sudo bash deploy/scripts/install_units.sh (always spell out `/srv/arthasignal/.local/bin/uv`: `~` would expand to your own home, not the service user's)` |
| Pause everything | `sudo systemctl stop 'arthasignal-*.timer'` (start again with `install_units.sh`) |

**Restoring a backup** (on any machine that has your private key):

```bash
age -d -i ~/arthasignal-backup-key.txt arthasignal_YYYYMMDD_HHMMSS.dump.age | tar -xf -
pg_restore --dbname <empty_database> --no-owner db.dump
```

## Part 10: Updating a server restored before 2026-10-04 13:02 (do this before Monday 2026-10-05 15:35)

A server restored from a dump taken before the sector fix has two problems:
- **111 mutual funds and debentures still carry their sponsor bank's equity sector**, for example CSY as "Commercial Banks";
- it runs older code, without the research-URL fix (12:39), the sector fix and the `sectors` chain step (13:02), or the rehearsal mode.

Run these on the server, in order. Every command is safe to repeat.

```bash
cd /srv/arthasignal/app
RUN='sudo -u arthasignal bash -lc'
ENV='cd /srv/arthasignal/app && set -a && . ./.env && set +a &&'

# 1. Take a backup first
sudo systemctl start arthasignal-backup.service && sudo ls -lh /var/backups/arthasignal | tail -2

# 2. New code and packages (lightgbm and scikit-learn were added)
sudo -u arthasignal git pull
sudo -u arthasignal /srv/arthasignal/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
sudo bash deploy/scripts/install_units.sh

# 3. Look first: the sectors check should FAIL (exit 1) on an unfixed database
$RUN "$ENV .venv/bin/python -m src.pipeline.data_quality --sectors"; echo "exit $?"

# 4. Migration: dry run, then for real
$RUN "$ENV .venv/bin/python deploy/migrations/m001_nonequity_sector_relabel.py --dry-run"
$RUN "$ENV .venv/bin/python deploy/migrations/m001_nonequity_sector_relabel.py"; echo "exit $?"

# 5. Run it again: it must say "already fixed: nothing to do" and change 0 rows
$RUN "$ENV .venv/bin/python deploy/migrations/m001_nonequity_sector_relabel.py"; echo "exit $?"

# 6. The sectors check must now pass with exit 0
$RUN "$ENV .venv/bin/python -m src.pipeline.data_quality --sectors"; echo "exit $?"
```

What to expect (this was tested on a scratch restore of the pre-fix dump of 11:41 on 2026-10-04):

| Step | Expected output |
| --- | --- |
| 3 | `DATA QUALITY FAILURE: 111 non-equity symbols carry an equity sector: C30MF, CSY, …`, exit 1 |
| 4 (dry run) | `"status": "dry run: nothing written"`, before 111 |
| 4 (real) | `"status": "applied"`, `"rows_changed": 111` (Mutual Funds 34, Non-Convertible Debentures 77), after 0, exit 0 |
| 5 | `"status": "already fixed: nothing to do"`, `"rows_changed": 0`, exit 0 |
| 6 | `sector check passed: no non-equity symbol carries an equity sector`, exit 0 |

The migration:
- runs in one transaction under an advisory lock;
- prints the before and after counts by instrument type and sector;
- logs a row in `ops_migration_runs` only when it changes something;
- exits 1 if anything is still mislabelled afterwards.

If step 6 does not print exit 0, **do not** let Monday's chain run: `sudo systemctl stop arthasignal-daily.timer`, and send me the output of steps 3-6.

## Security checklist

- [ ] The security group has exactly one inbound rule: SSH from your IP.
- [ ] `sudo ufw status` shows only OpenSSH allowed.
- [ ] `sudo ss -ltnp | grep 5432` shows only `127.0.0.1`.
- [ ] `.env`, `/etc/arthasignal/*` and `.pgpass` are `chmod 600` or `640`, and none of them is in git.
- [ ] The backup private key is **not** on the server.
- [ ] Ubuntu's default for EC2 AMIs is key-only SSH login. Check it with `sudo sshd -T | grep passwordauthentication`, which should print `passwordauthentication no`.

## What this kit does not do

- It does not deploy the website or API (`frontend/`, `src/api`), or the legacy `run_all_daily` website signals and Google Drive backup.
- It does not schedule the social collectors or LLM labelling: they need keys (`docs/LIVE_COLLECTORS.md`).
- It does not decide holidays: `config/nepse_calendar.json` has none yet, so the health check and capture may report a "missing" session on a real holiday until you add it.

## Check of the kit on the laptop (2026-10-04)

See `docs/PHASE_LOG.md`, Phase E-B, for the real outputs: the dump, a full restore into a scratch database with every table's row count compared, the ops dry runs and the tests.
