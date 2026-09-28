# 低等待流水线消融：统一页面、模型与 ASR，比较视觉并行和首段抢跑。
import argparse
import hashlib
import json
import math
import statistics
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "secrets/manga/0009.jpg", ROOT / "secrets/manga/0012.jpg", ROOT / "secrets/manga/0041.jpg"]
CFG = json.loads((ROOT / "config/reader.json").read_text(encoding="utf-8"))
BASE = "http://127.0.0.1:8765"
AUTH = {"X-Reader-Token": CFG["token"]}
MODES = {
    "all_off": (False, False),
    "vision_only": (True, False),
    "eager_only": (False, True),
    "all_on": (True, True),
}


def call(path, method="GET", body=None, headers=None, timeout=360):
    request = urllib.request.Request(
        BASE + path, data=body, method=method, headers={**AUTH, **(headers or {})}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), time.perf_counter() - started


def percentile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if out.exists():
        raise SystemExit("证据目录已存在，拒绝覆盖：" + str(out))
    out.mkdir(parents=True)
    call("/warmup", "POST", b"")
    records = []
    # 每轮反转次序，降低温度与缓存随时间单向漂移造成的偏差。
    names = list(MODES)
    for repeat in range(args.repeats):
        order = names if repeat % 2 == 0 else list(reversed(names))
        for name in order:
            parallel, eager = MODES[name]
            for source in PAGES:
                headers = {
                    "Content-Type": "image/jpeg",
                    "X-Reader-Parallel-Vision": "on" if parallel else "off",
                    "X-Reader-Eager-First-Audio": "on" if eager else "off",
                    "X-Reader-ASR-Check": "all",
                    "X-Reader-Speech-Speed": "1.00",
                }
                payload_raw, page_seconds = call("/pages", "POST", source.read_bytes(), headers)
                payload = json.loads(payload_raw)
                page_id = payload["page_id"]
                try:
                    audio, audio_seconds = call(
                        f"/pages/{page_id}/audio/0",
                        headers={"X-Reader-ASR-Check": "all", "X-Reader-Speech-Speed": "1.00"},
                    )
                finally:
                    call("/pages/" + page_id, "DELETE")
                row = {
                    "repeat": repeat + 1,
                    "mode": name,
                    "page": source.stem,
                    "parallel_vision": parallel,
                    "eager_first_audio": eager,
                    "page_seconds": round(page_seconds, 4),
                    "audio_get_seconds": round(audio_seconds, 4),
                    "first_audio_seconds": round(page_seconds + audio_seconds, 4),
                    "sentences": len(payload["sentences"]),
                    "sentences_sha256": hashlib.sha256(
                        json.dumps(payload["sentences"], ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "audio_sha256": hashlib.sha256(audio).hexdigest(),
                }
                records.append(row)
                print(name, repeat + 1, source.stem, row["first_audio_seconds"], flush=True)
    summaries = {}
    for name in names:
        selected = [row for row in records if row["mode"] == name]
        first = [row["first_audio_seconds"] for row in selected]
        pages = [row["page_seconds"] for row in selected]
        summaries[name] = {
            "samples": len(selected),
            "first_audio_median_seconds": round(statistics.median(first), 4),
            "first_audio_p95_seconds": round(percentile(first, .95), 4),
            "page_median_seconds": round(statistics.median(pages), 4),
            "page_p95_seconds": round(percentile(pages, .95), 4),
        }
    per_page_hashes = {}
    for source in PAGES:
        selected = [row for row in records if row["page"] == source.stem]
        per_page_hashes[source.stem] = {
            "sentence_hashes": sorted(set(row["sentences_sha256"] for row in selected)),
            "sentence_counts": sorted(set(row["sentences"] for row in selected)),
        }
    result = {
        "repeats": args.repeats,
        "pages": [source.stem for source in PAGES],
        "summaries": summaries,
        "per_page_output_equivalence": per_page_hashes,
        "records": records,
    }
    (out / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = ["# 低等待流水线消融", "", "每种组合三轮、每轮三页；页面与首段音频均走正式 HTTP、ASR 开启。", "", "| 模式 | 样本 | 首音中位数 | 首音 P95 | OCR/页面中位数 | OCR/页面 P95 |", "|---|---:|---:|---:|---:|---:|"]
    for name in names:
        row = summaries[name]
        lines.append(
            f"| {name} | {row['samples']} | {row['first_audio_median_seconds']:.4f}s | {row['first_audio_p95_seconds']:.4f}s | {row['page_median_seconds']:.4f}s | {row['page_p95_seconds']:.4f}s |"
        )
    lines += ["", "`per_page_output_equivalence` 中每页只能有一个句子哈希与句数，才表示四种组合输出一致。", ""]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
