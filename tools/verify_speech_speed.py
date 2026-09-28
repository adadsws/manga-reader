# 使用固定版 GPT-SoVITS 与 ASR 守卫实测三档语速；输出音频和可复核审计。
import argparse
import hashlib
import json
import wave
from pathlib import Path

from server.app import synthesize


ROOT = Path(__file__).resolve().parents[1]


def wav_seconds(body, target):
    target.write_bytes(body)
    with wave.open(str(target), "rb") as audio:
        return round(audio.getnframes() / audio.getframerate(), 3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="~outputs-intermediate/evidence/speech-speed-opacity-20260922")
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    text = "今天我们一起去图书馆，然后回家吃晚饭。"
    results = []
    for speed in (0.50, 1.00, 1.50):
        audit = []
        body = synthesize(text, audit=audit, asr_check=True, speed_factor=speed)
        target = out / f"speed-{speed:.2f}.wav"
        results.append(
            {
                "speed_factor": speed,
                "seconds": wav_seconds(body, target),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "asr_checked": bool(audit) and all(row.get("asr_checked") for row in audit),
                "audit": audit,
                "audio": target.name,
            }
        )
    durations = [row["seconds"] for row in results]
    if not durations[0] > durations[1] > durations[2]:
        raise RuntimeError(f"语速与实际时长不单调：{durations}")
    if not all(row["asr_checked"] for row in results):
        raise RuntimeError("存在未经过 ASR 完整性检查的语速样本")
    report = {
        "text": text,
        "speed_range": [0.50, 1.50],
        "duration_monotonic": True,
        "samples": results,
    }
    (out / "speech-speed.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
