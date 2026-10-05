#!/usr/bin/env bash
set -euo pipefail
BUCKET="$1"
mkdir -p ~/work/out/vlm
[[ -f ~/work/manifest_remote.json ]] || { echo "run run_surya.sh first, or prepare ~/work/manifest_remote.json" >&2; exit 1; }
~/ocr/bin/python ~/arthasignal/scripts/gpu/run_vlm.py ~/work/manifest_remote.json ~/work/out/vlm
aws s3 sync ~/work/out/vlm "$BUCKET/out/vlm"
