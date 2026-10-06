from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import date
from pathlib import Path
from typing import Any

from src.database.holdout_guard import HOLDOUT_START

ROOT = Path(__file__).resolve().parents[2]
LABEL_SET = ROOT / "docs" / "labels" / "label_set.json"
DERIVED = Path("~/Desktop/arthasignal-ai/derived/ocr").expanduser()
MANIFEST = DERIVED / "manifest.json"
PILOT = DERIVED / "pilot_manifest.json"
ARCHIVE = DERIVED / "pilot_images.tar"
SIZE = 200
KEYS = ("sha256", "path", "symbol", "published_date", "fiscal_year", "quarter")


def _year(item: dict[str, Any]) -> str:
    return str(item.get("published_date") or "")[:4]


def _before_holdout(item: dict[str, Any]) -> bool:
    return date.fromisoformat(str(item["published_date"])[:10]) < HOLDOUT_START


def build(labelled: list[dict[str, Any]], pool: list[dict[str, Any]], size: int = SIZE) -> list[dict[str, Any]]:
    chosen = {item["sha256"]: {k: item.get(k) for k in KEYS} | {"labelled": True, "label_id": item.get("label_id")} for item in labelled}
    if len(chosen) > size:
        raise ValueError(f"{len(chosen)} labelled reports do not fit in a pilot of {size}")
    rest = [item for item in pool if item["sha256"] not in chosen and _before_holdout(item)]
    by_year: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(rest, key=lambda i: hashlib.sha256(i["sha256"].encode()).hexdigest()):
        by_year.setdefault(_year(item), []).append(item)
    years = sorted(by_year)
    while len(chosen) < size and any(by_year.values()):
        for year in years:
            if by_year[year] and len(chosen) < size:
                item = by_year[year].pop(0)
                chosen[item["sha256"]] = {k: item.get(k) for k in KEYS} | {"labelled": False}
    return sorted(chosen.values(), key=lambda i: (not i["labelled"], i["sha256"]))


def write_archive(items: list[dict[str, Any]], target: Path = ARCHIVE) -> int:
    with tarfile.open(target, "w") as archive:
        for item in items:
            path = Path(item["path"])
            archive.add(path, arcname=f"{item['sha256'][:2]}/{path.name}")
    return target.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the GPU pilot: every labelled report plus unlabelled ones spread across years")
    parser.add_argument("--size", type=int, default=SIZE)
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args()
    labelled = json.loads(LABEL_SET.read_text())
    items = build(labelled, json.loads(MANIFEST.read_text()), args.size)
    missing = [i["sha256"] for i in items if not Path(i["path"]).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} pilot images are missing locally, for example {missing[0]}")
    PILOT.write_text(json.dumps(items, indent=1, default=str))
    out = {"pilot": str(PILOT), "images": len(items), "labelled": sum(i["labelled"] for i in items),
           "by_year": {y: sum(_year(i) == y for i in items) for y in sorted({_year(i) for i in items})}}
    if not args.no_archive:
        out["archive"] = str(ARCHIVE)
        out["archive_mb"] = round(write_archive(items) / 1e6, 1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
