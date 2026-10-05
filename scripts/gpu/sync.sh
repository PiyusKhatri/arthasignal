#!/usr/bin/env bash
set -euo pipefail
usage() { echo "usage: $0 up|down BUCKET (e.g. s3://my-bucket/arthasignal-ocr)" >&2; exit 2; }
[[ $# -eq 2 ]] || usage
LOCAL="$HOME/Desktop/arthasignal-ai"
case "$1" in
  up)
    aws s3 sync "$LOCAL/raw/archive/sharesansar_reports" "$2/images" --exclude "*.tmp"
    aws s3 cp "$LOCAL/derived/ocr/manifest.json" "$2/manifest.json" ;;
  down)
    aws s3 sync "$2/out/surya" "$LOCAL/derived/ocr/surya"
    aws s3 sync "$2/out/vlm" "$LOCAL/derived/ocr/vlm_qwen25vl7b" ;;
  *) usage ;;
esac
