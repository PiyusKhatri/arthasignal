from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
LABEL_SET = ROOT / "docs" / "labels" / "label_set.json"
LABELS = ROOT / "docs" / "labels" / "labels"
DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
NUMERIC = ["net_profit", "eps", "book_value_per_share", "net_worth", "reserves", "paid_up_capital", "npl_ratio", "capital_adequacy"]
FIELD_HELP = {
    "net_profit": "Net profit / (loss) for the period, year to date, as printed",
    "eps": "Earnings per share (Rs) as printed",
    "book_value_per_share": "Net worth (book value) per share (Rs)",
    "net_worth": "Total equity / net worth, as printed",
    "reserves": "Reserves (reserve and surplus total), as printed",
    "paid_up_capital": "Paid-up (share) capital, as printed",
    "npl_ratio": "Banks only: NPL to total loan (%)",
    "capital_adequacy": "Banks only: capital fund to RWA / CAR (%)",
}
CHOICES = {
    "period": ["quarterly", "annual"],
    "quarter": ["1", "2", "3", "4", ""],
    "basis": ["standalone", "group", "only one entity shown"],
    "unit": ["rupees", "thousands", "lakhs", "millions"],
    "eps_annualized": ["yes", "no", "not stated"],
    "language": ["english", "nepali", "mixed"],
    "legibility": ["clear", "partly legible", "unreadable"],
}


def number(raw: str) -> float | None:
    cleaned = raw.translate(DEVANAGARI).strip().replace(",", "")
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")") or cleaned.startswith("-")
    cleaned = cleaned.strip("()-").replace("/", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        raise ValueError(raw)
    value = float(cleaned)
    return -value if negative else value


def label_set() -> list[dict[str, Any]]:
    return json.loads(LABEL_SET.read_text())


def saved(label_id: str) -> dict[str, Any]:
    path = LABELS / f"{label_id}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(item: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {"label_id": item["label_id"], "sha256": item["sha256"], "symbol": item["symbol"], "fiscal_year_listed": item.get("fiscal_year"),
                              "labelled_at": datetime.now(timezone.utc).isoformat(), "errors": []}
    for key in CHOICES:
        record[key] = form.get(key, "")
    record["fiscal_year"] = form.get("fiscal_year", "").strip()
    record["notes"] = form.get("notes", "").strip()
    for field in NUMERIC:
        record[f"{field}_absent"] = form.get(f"{field}_absent") == "on"
        raw = form.get(field, "")
        record[f"{field}_raw"] = raw
        try:
            record[field] = number(raw)
        except ValueError:
            record[field] = None
            record["errors"].append(f"{field}: '{raw}' is not a number")
    LABELS.mkdir(parents=True, exist_ok=True)
    (LABELS / f"{item['label_id']}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return record


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>
body{{font-family:system-ui,sans-serif;margin:0}} .wrap{{display:flex;height:100vh}} .doc{{flex:1.6;overflow:auto;background:#333}}
.doc img{{transform-origin:0 0}} .form{{flex:1;overflow:auto;padding:12px 16px;border-left:1px solid #ccc}} label{{display:block;margin-top:8px;font-size:13px}}
input[type=text]{{width:60%}} .bar{{position:sticky;top:0;background:#222;padding:6px;z-index:2}} .bar button{{margin-right:6px}}
table{{border-collapse:collapse}} td,th{{border:1px solid #ccc;padding:4px 8px;font-size:13px}} .err{{color:#b00}} .done{{color:#070}}
</style></head><body>{body}</body></html>""".encode()


def index_page() -> bytes:
    rows = []
    for item in label_set():
        record = saved(item["label_id"])
        state = "<span class=done>labelled</span>" if record else "todo"
        if record.get("errors"):
            state = "<span class=err>has errors</span>"
        rows.append(f"<tr><td><a href='/label/{item['label_id']}'>{item['label_id']}</a></td><td>{html.escape(str(item['symbol']))}</td>"
                    f"<td>{item['year']}</td><td>{item['kind']}</td><td>{item['language']}</td><td>{html.escape(str(item['sector']))}</td><td>{state}</td></tr>")
    done = sum(1 for item in label_set() if saved(item["label_id"]))
    return page("Report labels", f"<div style='padding:16px'><h2>Report labels: {done} of {len(label_set())} done</h2>"
                f"<table><tr><th>id</th><th>symbol</th><th>year</th><th>kind</th><th>language</th><th>sector</th><th>status</th></tr>{''.join(rows)}</table></div>")


def label_page(index: int) -> bytes:
    items = label_set()
    item = items[index]
    record = saved(item["label_id"])
    prev_id = items[index - 1]["label_id"] if index > 0 else None
    next_id = items[index + 1]["label_id"] if index + 1 < len(items) else None

    def select(name: str) -> str:
        current = record.get(name) or (item["kind"] if name == "period" else str(item["quarter"]) if name == "quarter" and item.get("quarter") == item.get("quarter") and item.get("quarter") is not None else "")
        options = "".join(f"<option{' selected' if o == current else ''}>{o}</option>" for o in CHOICES[name])
        return f"<label>{name} <select name='{name}'>{options}</select></label>"

    fields = []
    for field in NUMERIC:
        value = html.escape(str(record.get(f"{field}_raw", "")))
        checked = " checked" if record.get(f"{field}_absent") else ""
        fields.append(f"<label><b>{field}</b> - {FIELD_HELP[field]}<br><input type=text name='{field}' value='{value}'> "
                      f"<input type=checkbox name='{field}_absent'{checked}> not in report</label>")
    errors = "".join(f"<div class=err>{html.escape(e)}</div>" for e in record.get("errors", []))
    source = f"/file/{item['label_id']}"
    viewer = (f"<embed src='{source}' type='application/pdf' width='100%' height='100%'>" if str(item["path"]).lower().endswith(".pdf")
              else f"<img id=doc src='{source}'>")
    body = f"""<div class=wrap><div class=doc><div class=bar>
<button onclick="z(1.25)">zoom +</button><button onclick="z(0.8)">zoom -</button><button onclick="s=1;z(1)">reset</button>
<a style='color:#fff' href='{source}' target=_blank>open full size</a></div>{viewer}</div>
<div class=form><a href='/'>index</a> | {item['label_id']} | {html.escape(str(item['symbol']))} | {html.escape(str(item['sector']))} | published {str(item['published_date'])[:10]} | listed FY {html.escape(str(item.get('fiscal_year')))} Q{item.get('quarter')}
{errors}<form method=post>
<label>fiscal_year (as printed, e.g. 2079/80) <input type=text name=fiscal_year value='{html.escape(record.get('fiscal_year') or str(item.get('fiscal_year') or ''))}'></label>
{select('period')}{select('quarter')}{select('basis')}{select('unit')}{''.join(fields)}{select('eps_annualized')}{select('language')}{select('legibility')}
<label>notes<br><textarea name=notes rows=3 cols=40>{html.escape(record.get('notes', ''))}</textarea></label>
<p><button name=go value=save>save</button> <button name=go value=next>save and next</button>
{f"<a href='/label/{prev_id}'>previous</a>" if prev_id else ''} {f"<a href='/label/{next_id}'>next</a>" if next_id else ''}</p></form>
<p style='font-size:12px'>Enter numbers exactly as printed (commas, Devanagari digits and brackets for losses are accepted). Choose the unit of the
statement once. If both group and bank columns exist, label the column named in <i>basis</i> and use it for every field. Tick "not in report" when a figure is not printed.</p></div></div>
<script>var s=1;function z(f){{s*=f;var d=document.getElementById('doc');if(d)d.style.transform='scale('+s+')';}}</script>"""
    return page(item["label_id"], body)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _index_of(self, label_id: str) -> int | None:
        for number_, item in enumerate(label_set()):
            if item["label_id"] == label_id:
                return number_
        return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, index_page())
        match = re.fullmatch(r"/(label|file)/(L\d{3})", path)
        index = self._index_of(match.group(2)) if match else None
        if index is None:
            return self._send(404, b"not found", "text/plain")
        if match.group(1) == "label":
            return self._send(200, label_page(index))
        file_path = Path(label_set()[index]["path"])
        return self._send(200, file_path.read_bytes(), mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")

    def do_POST(self) -> None:
        match = re.fullmatch(r"/label/(L\d{3})", urlparse(self.path).path)
        index = self._index_of(match.group(1)) if match else None
        if index is None:
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()}
        items = label_set()
        record = save(items[index], form)
        target = index + 1 if form.get("go") == "next" and not record["errors"] and index + 1 < len(items) else index
        self.send_response(303)
        self.send_header("Location", f"/label/{items[target]['label_id']}")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"labeling tool on http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()
