from __future__ import annotations

import argparse
import json
from pathlib import Path

PROMPT = (
    "You are reading a Nepali listed company's published financial report (quarterly or annual). Return only JSON with these keys: "
    "fiscal_year, quarter, unit (rupees, thousands, lakhs or millions), basis (standalone or group), net_profit_ytd, eps, eps_annualized (true/false/null), "
    "book_value_per_share, net_worth, reserves, paid_up_capital, npl_ratio, capital_adequacy. Copy every number exactly as printed, without converting units; "
    "Devanagari digits must be written as ASCII digits. Use null when a figure is not printed. Do not compute or infer any value."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("out_dir")
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--batch", type=int, default=16)
    args = parser.parse_args()
    from PIL import Image
    from vllm import LLM, SamplingParams

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = [i for i in json.loads(Path(args.manifest).read_text()) if not (out / f"{i['sha256']}.json").exists()]
    llm = LLM(model=args.model, max_model_len=16384, limit_mm_per_prompt={"image": 1})
    params = SamplingParams(temperature=0.0, max_tokens=512)
    for start in range(0, len(items), args.batch):
        chunk = items[start: start + args.batch]
        requests = [{"prompt": f"<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>{PROMPT}<|im_end|>\n<|im_start|>assistant\n",
                     "multi_modal_data": {"image": Image.open(i["path"]).convert("RGB")}} for i in chunk]
        for item, result in zip(chunk, llm.generate(requests, params)):
            (out / f"{item['sha256']}.json").write_text(json.dumps({"sha256": item["sha256"], "model": args.model, "raw": result.outputs[0].text}))
        print(start + len(chunk), "of", len(items), flush=True)


if __name__ == "__main__":
    main()
