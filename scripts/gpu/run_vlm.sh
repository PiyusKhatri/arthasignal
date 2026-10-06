#!/usr/bin/env bash
set -euo pipefail
BUCKET="$1"
MODE="${2:-full}"
mkdir -p ~/work/out/vlm ~/work/out/timing
[[ -f ~/work/manifest_remote.json ]] || { echo "run run_surya.sh first, or prepare ~/work/manifest_remote.json" >&2; exit 1; }
START=$(date +%s)
~/ocr/bin/python ~/arthasignal/scripts/gpu/run_vlm.py ~/work/manifest_remote.json ~/work/out/vlm | tee ~/work/out/timing/vlm_${MODE}.log
echo "{\"job\": \"qwen2.5-vl-7b\", \"mode\": \"$MODE\", \"seconds\": $(( $(date +%s) - START )), \"images\": $(ls ~/work/out/vlm | wc -l)}" > ~/work/out/timing/vlm_${MODE}.json
aws s3 sync ~/work/out/vlm "$BUCKET/out/vlm"
aws s3 sync ~/work/out/timing "$BUCKET/out/timing"
