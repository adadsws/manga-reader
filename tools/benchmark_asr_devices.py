"""比较固定 Paraformer 在 CPU/CUDA 上的耗时与覆盖判定，不保存转写正文。"""

import gc
import hashlib
import json
import statistics
import time
from pathlib import Path

import torch
from funasr import AutoModel

from server.tts_guard import coverage


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "~archive/20260928-历史验收证据与旧计划/docs/evidence/asr-models-three-pages-20260921"
OUT = ROOT / "~outputs-intermediate/evidence/remaining-acceleration-20260923/asr-devices.json"


def main():
    inputs = json.loads((SOURCE / "inputs.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "config/asr-validation.lock.json").read_text(encoding="utf-8"))
    samples = []
    for page, rows in inputs.items():
        for index, row in enumerate(rows, 1):
            samples.append((page, index, row["text"], SOURCE / page / "units" / f"{index:03}" / "v4.wav"))

    routes = {}
    for device in ("cpu", "cuda"):
        if device == "cuda" and not torch.cuda.is_available():
            routes[device] = {"available": False, "reason": "torch.cuda.is_available() is false"}
            continue
        loaded = time.monotonic()
        try:
            model = AutoModel(
                model=lock["model_path"],
                device=device,
                disable_update=True,
                ncpu=4,
                disable_pbar=True,
            )
            load_seconds = time.monotonic() - loaded
            model.generate(input=str(samples[0][3]), disable_pbar=True)
            records = []
            for page, index, expected, path in samples:
                started = time.monotonic()
                result = model.generate(input=str(path), disable_pbar=True)
                seconds = time.monotonic() - started
                recognized = "".join(item.get("text", "") for item in result)
                check = coverage(expected, recognized)
                records.append(
                    {
                        "page": page,
                        "index": index,
                        "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "recognized_sha256": hashlib.sha256(recognized.encode("utf-8")).hexdigest(),
                        "passed": check["passed"],
                        "alignment": round(check["alignment"], 4),
                        "seconds": round(seconds, 4),
                    }
                )
            values = [record["seconds"] for record in records]
            routes[device] = {
                "available": True,
                "load_seconds": round(load_seconds, 4),
                "total_seconds": round(sum(values), 4),
                "median_seconds": round(statistics.median(values), 4),
                "passed": sum(record["passed"] for record in records),
                "samples": len(records),
                "records": records,
            }
        except Exception as exc:
            routes[device] = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            if "model" in locals():
                del model
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    if routes.get("cpu", {}).get("available") and routes.get("cuda", {}).get("available"):
        cpu = routes["cpu"]["total_seconds"]
        cuda = routes["cuda"]["total_seconds"]
        same = all(
            left["recognized_sha256"] == right["recognized_sha256"]
            for left, right in zip(routes["cpu"]["records"], routes["cuda"]["records"])
        )
        comparison = {
            "cuda_faster_percent": round((cpu - cuda) / cpu * 100, 2),
            "recognized_hashes_equal": same,
            "coverage_results_equal": [r["passed"] for r in routes["cpu"]["records"]]
            == [r["passed"] for r in routes["cuda"]["records"]],
        }
    else:
        comparison = None
    result = {"routes": routes, "comparison": comparison}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"routes": {k: {x: y for x, y in v.items() if x != "records"} for k, v in routes.items()}, "comparison": comparison}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
