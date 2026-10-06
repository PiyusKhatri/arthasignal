#!/usr/bin/env bash
set -euo pipefail
BUCKET="$1"
MODE="${2:-full}"
mkdir -p ~/work/images ~/work/out/surya ~/work/out/timing
if [[ "$MODE" == "pilot" ]]; then
  aws s3 cp "$BUCKET/pilot_images.tar" ~/work/pilot_images.tar
  tar -xf ~/work/pilot_images.tar -C ~/work/images
  aws s3 cp "$BUCKET/pilot_manifest.json" ~/work/manifest.json
else
  aws s3 sync "$BUCKET/images" ~/work/images
  aws s3 cp "$BUCKET/manifest.json" ~/work/manifest.json
fi
~/ocr/bin/python - <<'PY'
import json, pathlib
m = json.load(open(pathlib.Path.home() / "work/manifest.json"))
root = pathlib.Path.home() / "work/images"
for item in m:
    item["path"] = str(root / item["sha256"][:2] / pathlib.Path(item["path"]).name)
json.dump(m, open(pathlib.Path.home() / "work/manifest_remote.json", "w"))
PY
START=$(date +%s)
~/ocr/bin/python ~/arthasignal/scripts/ocr/ocr_run.py batch ~/work/manifest_remote.json surya ~/work/out/surya | tee ~/work/out/timing/surya_${MODE}.log
echo "{\"job\": \"surya\", \"mode\": \"$MODE\", \"seconds\": $(( $(date +%s) - START )), \"images\": $(ls ~/work/out/surya | wc -l)}" > ~/work/out/timing/surya_${MODE}.json
aws s3 sync ~/work/out/surya "$BUCKET/out/surya"
aws s3 sync ~/work/out/timing "$BUCKET/out/timing"
