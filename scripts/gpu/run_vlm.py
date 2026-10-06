from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

PROMPT = (
    "You are reading a Nepali listed company's published financial report (quarterly or annual). Return only JSON with these keys: "
    "fiscal_year, quarter, unit (rupees, thousands, lakhs, millions or crore; the unit line printed above the statement the figure comes from), "
    "basis (standalone or group; when both columns are printed use the standalone, bank or company column for every figure), "
    "net_profit_ytd (net profit or loss for the period up to the end of this quarter, not the this-quarter-only column, negative when printed in brackets), "
    "eps, eps_annualized (true/false/null), "
    "book_value_per_share, net_worth, reserves, paid_up_capital, npl_ratio, capital_adequacy. Copy every number exactly as printed, without converting units; "
    "Devanagari digits must be written as ASCII digits. Use null when a figure is not printed. Do not compute or infer any value."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("out_dir")
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--max-pixels", type=int, default=3_000_000)
    args = parser.parse_args()
    from PIL import Image
    from vllm import LLM, SamplingParams

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = [i for i in json.loads(Path(args.manifest).read_text()) if not (out / f"{i['sha256']}.json").exists()]
    llm = LLM(model=args.model, max_model_len=16384, limit_mm_per_prompt={"image": 1}, gpu_memory_utilization=0.92,
              mm_processor_kwargs={"min_pixels": 256 * 28 * 28, "max_pixels": args.max_pixels})
    started = time.time()
    params = SamplingParams(temperature=0.0, max_tokens=512)
    for start in range(0, len(items), args.batch):
        chunk = items[start: start + args.batch]
        requests = [{"prompt": f"<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>{PROMPT}<|im_end|>\n<|im_start|>assistant\n",
                     "multi_modal_data": {"image": Image.open(i["path"]).convert("RGB")}} for i in chunk]
        for item, result in zip(chunk, llm.generate(requests, params)):
            (out / f"{item['sha256']}.json").write_text(json.dumps({"sha256": item["sha256"], "model": args.model, "raw": result.outputs[0].text}))
        done = start + len(chunk)
        print(done, "of", len(items), f"{(time.time() - started) / done:.1f} s per image", flush=True)


if __name__ == "__main__":
    main()
