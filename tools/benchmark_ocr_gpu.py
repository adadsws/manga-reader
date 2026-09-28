"""以相同页面交错比较 CPU/GPU RapidOCR；只保存耗时、计数与哈希。"""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from rapidocr import RapidOCR

from server.ocr import MangaOCR


ROOT = Path(__file__).resolve().parents[1]
PAGES = ("0009.jpg", "0012.jpg", "0041.jpg")


def semantic_hash(rows):
    payload = [
        {
            "original": row["original"],
            "text": row["text"],
            "panel_id": row.get("panel_id"),
        }
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=("cpu", "gpu", "hybrid"), default=("cpu", "gpu", "hybrid"))
    parser.add_argument("--append", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        raise ValueError("repeats 必须至少为 1")

    reader = MangaOCR("cpu")
    engines = {
        "cpu": reader.engine,
        "gpu": RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": True}),
    }
    providers = {
        mode: MangaOCR._providers(engine) for mode, engine in engines.items()
    }
    providers["hybrid"] = {"main": providers["gpu"], "refine": providers["cpu"]}
    previous = None
    if args.append and args.append.exists():
        previous = json.loads(args.append.read_text(encoding="utf-8"))
        providers = {**previous.get("providers", {}), **providers}
    bodies = {name: (ROOT / "secrets/manga" / name).read_bytes() for name in PAGES}

    # 两条路线各自预热一次；交错次序抵消持续运行时的宿主漂移。
    routes = {
        "cpu": (engines["cpu"], engines["cpu"]),
        "gpu": (engines["gpu"], engines["gpu"]),
        "hybrid": (engines["gpu"], engines["cpu"]),
    }
    for mode in args.modes:
        main_engine, refine_engine = routes[mode]
        reader.engine = main_engine
        reader.refine_engine = refine_engine
        reader.ocr_backend = "cuda" if mode != "cpu" else "cpu"
        reader.read(bodies[PAGES[0]], parallel=True)

    records = list(previous.get("records", [])) if previous else []
    for repeat in range(args.repeats):
        modes = tuple(args.modes) if repeat % 2 == 0 else tuple(reversed(args.modes))
        for mode in modes:
            reader.engine, reader.refine_engine = routes[mode]
            reader.ocr_backend = "cuda" if mode != "cpu" else "cpu"
            for page in PAGES:
                started = time.monotonic()
                rows = reader.read(bodies[page], parallel=True)
                records.append(
                    {
                        "repeat": repeat + 1,
                        "mode": mode,
                        "page": page,
                        "screen_sha256": hashlib.sha256(bodies[page]).hexdigest(),
                        "semantic_sha256": semantic_hash(rows),
                        "rows": len(rows),
                        "wall_seconds": round(time.monotonic() - started, 4),
                        "timings": reader.last_timings,
                    }
                )

    summaries = {}
    recorded_modes = sorted({record["mode"] for record in records})
    for mode in recorded_modes:
        samples = [record["wall_seconds"] for record in records if record["mode"] == mode]
        summaries[mode] = {
            "samples": len(samples),
            "median_seconds": round(statistics.median(samples), 4),
            "mean_seconds": round(statistics.mean(samples), 4),
        }
    cpu_median = summaries["cpu"]["median_seconds"]
    selected_mode = min(summaries, key=lambda mode: summaries[mode]["median_seconds"])
    selected_median = summaries[selected_mode]["median_seconds"]
    equivalence = {
        page: len(
            {
                record["semantic_sha256"]
                for record in records
                if record["page"] == page
            }
        )
        == 1
        for page in PAGES
    }
    result = {
        "repeats": args.repeats,
        "pages": list(PAGES),
        "providers": providers,
        "summaries": summaries,
        "selected_mode": selected_mode,
        "selected_median_faster_percent": round((cpu_median - selected_median) / cpu_median * 100, 2),
        "semantic_equivalence": equivalence,
        "records": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("providers", "summaries", "selected_mode", "selected_median_faster_percent", "semantic_equivalence")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
