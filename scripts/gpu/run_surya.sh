#!/usr/bin/env bash
set -euo pipefail
BUCKET="$1"
mkdir -p ~/work/images ~/work/out/surya
aws s3 sync "$BUCKET/images" ~/work/images
aws s3 cp "$BUCKET/manifest.json" ~/work/manifest.json
~/ocr/bin/python - <<'PY'
import json, pathlib
m = json.load(open(pathlib.Path.home() / "work/manifest.json"))
root = pathlib.Path.home() / "work/images"
for item in m:
    item["path"] = str(root / item["sha256"][:2] / pathlib.Path(item["path"]).name)
json.dump(m, open(pathlib.Path.home() / "work/manifest_remote.json", "w"))
PY
~/ocr/bin/python ~/arthasignal/scripts/ocr/ocr_run.py batch ~/work/manifest_remote.json surya ~/work/out/surya
aws s3 sync ~/work/out/surya "$BUCKET/out/surya"
