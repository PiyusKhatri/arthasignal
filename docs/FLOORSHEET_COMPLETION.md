# Floorsheet completion: steps for when the VM is reachable

**Status on 2026-10-05:** not done, nothing merged.
- **The VM does not answer:** `ssh ubuntu@3.89.163.188` times out on port 22.
- **Likely cause:** the VM was stopped, so its public IP changed, or the security group only allows SSH from an older home IP. The Mac's public IP today is 27.34.65.91; check yours with `curl https://checkip.amazonaws.com`.
- **Terms:** the MeroLagani terms concern is resolved by the owner's written agreement (`docs/DATA_LICENSES.md`).

**What the VM collects** (`scripts/vm_floorsheet_jobs.txt` in `arthasignal-ai`): 2025-01-20 to 2026-08-30, plus 98 single days that failed earlier. Files from 2025-09-30 onward are holdout data. They may be stored, but research code never opens them (`floorsheet_files` refuses any end date in the holdout).

## 1. Make the VM reachable

1. AWS console → EC2 → Instances → select the floorsheet VM. If its state is *stopped*, choose Instance state → Start.
2. Copy its **Public IPv4 address** (it changes after every stop and start unless an Elastic IP is attached). Below it is `$IP`.
3. Allow SSH from your current IP: EC2 → the instance's **Security** tab → its security group → Edit inbound rules.
   - Find the rule *SSH, TCP 22* and set Source to **My IP** (the console fills in your current address as /32).
   - Delete any old single-IP SSH rules you no longer use, then Save rules.
4. Optional: Elastic IPs → Allocate → Associate with the instance, so the address stops changing.
5. Test: `ssh -i ~/Desktop/arthasignal-key.pem ubuntu@$IP 'echo ok'`.

## 2. Check that the backfill has finished (on the VM)

```bash
ssh -i ~/Desktop/arthasignal-key.pem ubuntu@$IP
systemctl status floorsheet-backfill@jobs --no-pager
ls ~/arthasignal-ai/db/vm_jobs/*.done | wc -l              # 99 when every range is finished
pgrep -f backfill_merolagani_floorsheet || echo "no backfill running"
grep ' event=' ~/arthasignal-ai/logs/merolagani_backfill.log | tail -5
```
If the job stopped because the VM was off, start it again (`sudo systemctl start floorsheet-backfill@jobs`) and wait. **Do not merge a partial run.**

## 3. Pull, verify and merge (on the Mac, in `~/Desktop/arthasignal-ai`)

```bash
scripts/vm_pull_raw.sh ubuntu@$IP            # stage only: copies, counts and checks every SHA-256
scripts/vm_pull_raw.sh ubuntu@$IP --merge    # copies new files into raw/; existing files are never overwritten
cat incoming/vm_raw_*/conflicts.txt          # same name, different content: keep both, compare before choosing
```

## 4. Audit (as in `docs/FLOORSHEET_AUDIT.md`)

```bash
find raw/floorsheet -name '*.parquet' | LC_ALL=C sort | xargs shasum -a 256 > docs/floorsheet_manifest_v2.sha256
```
Then report:
- per-year files, rows and zero-row files;
- `data_quality_gap_pct`;
- business-date versus file-date mismatches and duplicate contract numbers;
- the sessions in `daily_prices` (up to 2025-09-29) that still have no file.

## 5. Extend the derived OHLC and broker features (in `arthasignal`)

```bash
venv/bin/python -m src.simulation.floorsheet_ohlc build --end 2025-09-29     # derived bars, board lots, source floorsheet_derived
venv/bin/python -m src.backtest.broker_flow_features                         # broker-flow features
```
- **Derived bars:** the build refuses any end on or after 2025-09-30. The protocol (`docs/SIMULATION_PROTOCOL.md` section 4a) uses derived bars only before 2018-02-18; bars after that are kept for comparison with `daily_prices`.
- **Broker-flow features:** their development end (2025-01-19) is fixed in `src/backtest/broker_flow_spec.py`. Moving it to 2025-09-29 is a change to a pre-registered study and needs its own decision.

## 6. Report

Append to `docs/PHASE_LOG.md`:
- coverage per year before and after;
- conflicts;
- sessions still missing;
- the row counts of the extended derived bars.
