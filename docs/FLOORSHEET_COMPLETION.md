# Floorsheet completion: status and the steps left for the owner

**Status on 2026-10-04:** not done, and nothing was merged. There are two reasons.

1. **The VM did not answer.** `ssh -i ~/Desktop/arthasignal-key.pem ubuntu@3.89.163.188` timed out (port 22). The VM is stopped, or its public IP changed after a stop and start (`scripts/vm_instructions.md` in `arthasignal-ai` warns about this). The backfill status could not be checked, so by instruction nothing was pulled.
2. **Terms concern (owner decision needed first).** The floorsheet archive and the VM backfill both come from Merolagani (`scripts/backfill_merolagani_floorsheet.py`). Merolagani's Disclaimer/Terms page says users are "strictly prohibited from employing any automated data collection methods, including but not limited to scripts, APIs, screen scraping, data mining, robots, or other data gathering and extraction tools, regardless of their intended purposes". The 2,415 files already on the laptop were collected the same way. Decide whether to keep them, stop the VM job, and ask Merolagani for permission, before running the steps below.

**What the VM is collecting** (`scripts/vm_floorsheet_jobs.txt`): 2025-01-20 to 2026-08-30 (the 19-month hole), plus 98 single days that failed earlier (2014-2024). Everything from 2025-09-30 onward is holdout data: it may be stored, but research code must never open it.

## Steps, once the terms question is settled

Run from `~/Desktop/arthasignal-ai` on the Mac unless marked VM.

1. **Find the VM.** In the AWS console (EC2, Instances), start it if stopped and copy its public IPv4 address. Below it is `$VM` (for example `ubuntu@3.89.163.188`).
2. **Check that the backfill has finished** (VM):
   ```bash
   ssh -i ~/Desktop/arthasignal-key.pem $VM
   systemctl status floorsheet-backfill@jobs --no-pager
   ls ~/arthasignal-ai/db/vm_jobs/ | wc -l          # finished ranges (.done markers)
   wc -l ~/arthasignal-ai/scripts/vm_floorsheet_jobs.txt   # 99 ranges in total
   grep ' event=' ~/arthasignal-ai/logs/merolagani_backfill.log | tail -5
   pgrep -f backfill_merolagani_floorsheet || echo "no backfill running"
   ```
   It has finished when there are 99 `.done` markers and no process is running. **If it is still running, stop here and come back later. Do not merge a partial run.**
3. **Stage and verify only** (no merge). This copies every remote Parquet file, checks counts and SHA-256, and stops:
   ```bash
   scripts/vm_pull_raw.sh $VM
   ```
4. **Merge.** Existing files are never overwritten; a different file with the same name is listed as a conflict:
   ```bash
   scripts/vm_pull_raw.sh $VM --merge
   cat incoming/vm_raw_*/conflicts.txt
   ```
   Any line in `conflicts.txt` is a date where the VM and the laptop hold different files. Keep both copies and compare row counts, contract numbers and summed amounts before choosing one, as in `docs/FLOORSHEET_AUDIT.md`.
5. **Audit the new files** with the method of `docs/FLOORSHEET_AUDIT.md`:
   - new manifest: `find raw/floorsheet -name '*.parquet' | sort | xargs shasum -a 256 > docs/floorsheet_manifest_v2.sha256`;
   - per-year files, rows, zero-row files, `data_quality_gap_pct`, and the days still missing against `daily_prices` sessions;
   - business-date versus file-date mismatches and duplicate contract numbers.
6. **Extend the derived bars and features** (in `arthasignal`):
   - **Derived OHLC:** `python -m src.simulation.floorsheet_ohlc build` rebuilds the derived bars up to the configured verification end (2025-01-19). Extending them further means changing `floorsheet_ohlc.verification.end` in `config/simulation_protocol.yaml`, which needs a new protocol version. The protocol only uses derived bars before 2018-02-18, so this matters only for the 98 retried days before 2018.
   - **Broker-flow features:** they are rebuilt with `python -m src.backtest.broker_flow_features` and stop at their development end (2025-01-19).
7. **Report** the per-year coverage before and after, the conflicts, and the days still missing in `docs/PHASE_LOG.md`.
