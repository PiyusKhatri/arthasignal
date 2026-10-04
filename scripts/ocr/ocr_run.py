from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def tesseract(paths: list[Path]) -> dict[str, str]:
    out = {}
    for path in paths:
        result = subprocess.run(["tesseract", str(path), "stdout", "-l", "nep+eng", "--psm", "6"], capture_output=True, text=True)
        out[path.name] = result.stdout
    return out


def _lines_by_row(items: list[tuple[float, float, str]]) -> str:
    items.sort(key=lambda r: (r[0], r[1]))
    rows: list[list[tuple[float, float, str]]] = []
    for y, x, text in items:
        if rows and abs(rows[-1][0][0] - y) < 12:
            rows[-1].append((y, x, text))
        else:
            rows.append([(y, x, text)])
    return "\n".join("  ".join(t for _, _, t in sorted(row, key=lambda r: r[1])) for row in rows)


def paddle(paths: list[Path], mobile: bool = False) -> dict[str, str]:
    from paddleocr import PaddleOCR

    options = {"text_detection_model_name": "PP-OCRv5_mobile_det", "text_recognition_model_name": "en_PP-OCRv5_mobile_rec"} if mobile else {"lang": "en"}
    engine = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, **options)
    out = {}
    for path in paths:
        result = engine.predict(str(path))
        items = []
        for page in result:
            for text, box in zip(page["rec_texts"], page["rec_boxes"]):
                items.append((float(box[1]), float(box[0]), text))
        out[path.name] = _lines_by_row(items)
    return out


def _html_text(html: str) -> str:
    import html as html_lib
    import re

    html = re.sub(r"</t[dh]>", "  ", html, flags=re.I)
    html = re.sub(r"</tr>|<br\s*/?>|</p>|</h\d>|</li>|</div>", "\n", html, flags=re.I)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", html))


def surya(paths: list[Path]) -> dict[str, str]:
    from PIL import Image
    from surya.recognition import RecognitionPredictor

    recognition = RecognitionPredictor()
    out = {}
    for path in paths:
        image = Image.open(path).convert("RGB")
        page = recognition([image], full_page=True)[0]
        blocks = sorted(page.blocks, key=lambda b: b.reading_order)
        out[path.name] = "\n".join(_html_text(b.html) for b in blocks if b.html)
    return out


ENGINES = {"tesseract": tesseract, "paddleocr": paddle, "paddleocr_mobile": lambda paths: paddle(paths, mobile=True), "surya": surya}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("engine", choices=list(ENGINES))
    parser.add_argument("output")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    paths = [Path(item["path"]) for item in manifest]
    started = time.time()
    texts = ENGINES[args.engine](paths)
    Path(args.output).write_text(json.dumps({"engine": args.engine, "seconds": round(time.time() - started, 1), "texts": texts}, ensure_ascii=False))
    print(args.engine, len(texts), "documents", round(time.time() - started, 1), "s")




def batch_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("engine", choices=list(ENGINES))
    parser.add_argument("out_dir")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.manifest).read_text())
    todo = [Path(item["path"]) for item in manifest if not (out_dir / f"{item['sha256']}.txt").exists()]
    by_name = {Path(item["path"]).name: item["sha256"] for item in manifest}
    for start in range(0, len(todo), 20):
        chunk = todo[start: start + 20]
        texts = ENGINES[args.engine](chunk)
        for name, body in texts.items():
            target = out_dir / f"{by_name[name]}.txt"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(body)
            temporary.replace(target)
        print(args.engine, start + len(chunk), "of", len(todo), flush=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        sys.argv.pop(1)
        batch_main()
    else:
        main()
