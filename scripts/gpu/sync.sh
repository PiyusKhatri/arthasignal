#!/usr/bin/env bash
set -euo pipefail
usage() { echo "usage: $0 pilot|up|down BUCKET (e.g. s3://my-bucket/arthasignal-ocr)" >&2; exit 2; }
[[ $# -eq 2 ]] || usage
LOCAL="$HOME/Desktop/arthasignal-ai"
case "$1" in
  pilot)
    aws s3 cp "$LOCAL/derived/ocr/pilot_images.tar" "$2/pilot_images.tar"
    aws s3 cp "$LOCAL/derived/ocr/pilot_manifest.json" "$2/pilot_manifest.json" ;;
  up)
    python3 - "$LOCAL/derived/ocr/manifest.json" > /tmp/arthasignal_full_list.txt <<'PY'
import json, sys
from pathlib import Path
for item in json.load(open(sys.argv[1])):
    print(f"{item['sha256'][:2]}/{Path(item['path']).name}")
PY
    aws s3 sync "$LOCAL/raw/archive/sharesansar_reports" "$2/images" --exclude "*" $(sed 's/^/--include /' /tmp/arthasignal_full_list.txt | tr '\n' ' ')
    aws s3 cp "$LOCAL/derived/ocr/manifest.json" "$2/manifest.json" ;;
  down)
    aws s3 sync "$2/out/surya" "$LOCAL/derived/ocr/surya"
    aws s3 sync "$2/out/vlm" "$LOCAL/derived/ocr/vlm_qwen25vl7b"
    aws s3 sync "$2/out/timing" "$LOCAL/derived/ocr/gpu_timing" ;;
  *) usage ;;
esac
