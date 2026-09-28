"""在固定 V4、文本和 seed 下比较 GPT-SoVITS 原生并行参数。"""

import hashlib
import io
import json
import statistics
import time
import urllib.request
import wave
from pathlib import Path

from server.tts_guard import coverage, recognize


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "~archive/20260928-历史验收证据与旧计划/docs/evidence/asr-models-three-pages-20260921/inputs.json"
OUT = ROOT / "~outputs-intermediate/evidence/remaining-acceleration-20260923/tts-parameters.json"
VARIANTS = (
    ("serial-b1", False, 1),
    ("parallel-b1", True, 1),
    ("parallel-b2", True, 2),
    ("parallel-b4", True, 4),
)


def synthesize(config, text, parallel, batch):
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": config["reference_audio"],
        "prompt_text": config["reference_text"],
        "prompt_lang": config.get("prompt_lang", "zh"),
        "text_split_method": config.get("text_split_method", "cut0"),
        "batch_size": batch,
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": parallel,
        "speed_factor": 1.0,
        "seed": config.get("seed", 42),
        "repetition_penalty": config.get("repetition_penalty", 1.0),
        "top_k": config.get("top_k", 5),
    }
    request = urllib.request.Request(
        config["tts_url"] + "/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=240) as response:
        body = response.read()
    seconds = time.monotonic() - started
    with wave.open(io.BytesIO(body), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        complete = bool(wav.getnframes()) and len(frames) == wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
    return body, seconds, complete


def main():
    config = json.loads((ROOT / "config/reader.json").read_text(encoding="utf-8"))
    if str(config.get("model_version", "")).lower() != "v4":
        raise RuntimeError("本基准只允许在当前 V4 模型下运行")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    samples = [(page, index, row["text"]) for page, rows in source.items() for index, row in enumerate(rows, 1)]
    recognize((ROOT / "~archive/20260928-历史验收证据与旧计划/docs/evidence/asr-models-three-pages-20260921/0009/units/001/v4.wav").read_bytes())
    records = []
    for sample_number, (page, index, text) in enumerate(samples):
        variants = VARIANTS[sample_number % len(VARIANTS):] + VARIANTS[:sample_number % len(VARIANTS)]
        for name, parallel, batch in variants:
            try:
                body, seconds, complete = synthesize(config, text, parallel, batch)
                recognized = recognize(body)
                check = coverage(text, recognized)
                records.append(
                    {
                        "variant": name,
                        "parallel_infer": parallel,
                        "batch_size": batch,
                        "page": page,
                        "index": index,
                        "characters": len(text),
                        "seconds": round(seconds, 4),
                        "wav_complete": complete,
                        "audio_sha256": hashlib.sha256(body).hexdigest(),
                        "recognized_sha256": hashlib.sha256(recognized.encode("utf-8")).hexdigest(),
                        "coverage_passed": check["passed"],
                        "alignment": round(check["alignment"], 4),
                    }
                )
            except Exception as exc:
                records.append(
                    {
                        "variant": name,
                        "parallel_infer": parallel,
                        "batch_size": batch,
                        "page": page,
                        "index": index,
                        "characters": len(text),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    summaries = {}
    for name, parallel, batch in VARIANTS:
        rows = [record for record in records if record["variant"] == name]
        valid = [record for record in rows if "seconds" in record]
        values = [record["seconds"] for record in valid]
        summaries[name] = {
            "parallel_infer": parallel,
            "batch_size": batch,
            "samples": len(rows),
            "successful": len(valid),
            "coverage_passed": sum(bool(row.get("coverage_passed")) for row in valid),
            "wav_complete": sum(bool(row.get("wav_complete")) for row in valid),
            "total_seconds": round(sum(values), 4),
            "median_seconds": round(statistics.median(values), 4) if values else None,
        }
    baseline = summaries["serial-b1"]["total_seconds"]
    for summary in summaries.values():
        if summary["successful"] == len(samples):
            summary["faster_than_baseline_percent"] = round(
                (baseline - summary["total_seconds"]) / baseline * 100, 2
            )
    eligible = [
        name
        for name, summary in summaries.items()
        if summary["successful"] == len(samples)
        and summary["coverage_passed"] == len(samples)
        and summary["wav_complete"] == len(samples)
    ]
    selected = min(eligible, key=lambda name: summaries[name]["total_seconds"]) if eligible else None
    result = {"model_version": config["model_version"], "samples": len(samples), "summaries": summaries, "selected": selected, "records": records}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summaries": summaries, "selected": selected}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
