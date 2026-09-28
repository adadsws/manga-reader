"""在首遍覆盖失败样本上比较串行/并行 TTS 的完整 ASR 保护结果。"""

import hashlib
import io
import json
import statistics
import time
import urllib.request
import wave
from pathlib import Path

from server.tts_guard import guarded_synthesize


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "~archive/20260928-历史验收证据与旧计划/docs/evidence/asr-models-three-pages-20260921/inputs.json"
OUT = ROOT / "~outputs-intermediate/evidence/remaining-acceleration-20260923/tts-guarded-parameters.json"
FAILED = (("0009", 2), ("0009", 6), ("0012", 2), ("0041", 2))
VARIANTS = (("serial-b1", False), ("parallel-b1", True))


def tts_request(config, text, seed, parallel):
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": config["reference_audio"],
        "prompt_text": config["reference_text"],
        "prompt_lang": config.get("prompt_lang", "zh"),
        "text_split_method": config.get("text_split_method", "cut0"),
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": parallel,
        "speed_factor": 1.0,
        "seed": seed,
        "repetition_penalty": config.get("repetition_penalty", 1.0),
        "top_k": config.get("top_k", 5),
    }
    request = urllib.request.Request(
        config["tts_url"] + "/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        body = response.read()
    with wave.open(io.BytesIO(body), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
        if not wav.getnframes() or len(pcm) != wav.getnframes() * wav.getnchannels() * wav.getsampwidth():
            raise ValueError("TTS 返回的 WAV 不完整")
    return body


def main():
    config = json.loads((ROOT / "config/reader.json").read_text(encoding="utf-8"))
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = []
    for sample_number, (page, index) in enumerate(FAILED):
        text = source[page][index - 1]["text"]
        variants = VARIANTS[sample_number % 2 :] + VARIANTS[: sample_number % 2]
        for name, parallel in variants:
            started = time.monotonic()
            body, audit = guarded_synthesize(
                text,
                lambda part, seed, enabled=parallel: tts_request(config, part, seed, enabled),
                config.get("seed", 42),
            )
            records.append(
                {
                    "variant": name,
                    "page": page,
                    "index": index,
                    "characters": len(text),
                    "seconds": round(time.monotonic() - started, 4),
                    "attempts": len(audit["attempts"]),
                    "verified": audit["verified"],
                    "recovered": audit["recovered"],
                    "partial_recovery": audit.get("partial_recovery", False),
                    "fallback": audit.get("fallback"),
                    "audio_sha256": hashlib.sha256(body).hexdigest(),
                }
            )
    summaries = {}
    for name, parallel in VARIANTS:
        rows = [row for row in records if row["variant"] == name]
        values = [row["seconds"] for row in rows]
        summaries[name] = {
            "parallel_infer": parallel,
            "samples": len(rows),
            "verified": sum(row["verified"] for row in rows),
            "recovered": sum(row["recovered"] for row in rows),
            "partial_recovery": sum(row["partial_recovery"] for row in rows),
            "attempts": sum(row["attempts"] for row in rows),
            "total_seconds": round(sum(values), 4),
            "median_seconds": round(statistics.median(values), 4),
        }
    baseline = summaries["serial-b1"]["total_seconds"]
    summaries["parallel-b1"]["faster_than_serial_percent"] = round(
        (baseline - summaries["parallel-b1"]["total_seconds"]) / baseline * 100, 2
    )
    result = {"model_version": config["model_version"], "samples": len(FAILED), "summaries": summaries, "records": records}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
