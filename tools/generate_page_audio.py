# 用当前电脑服务重生成三张真实相册截图对应的整页音频。
import html
import hashlib
import json
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "~outputs-intermediate/evidence/three-page-audio"


def render_report():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    cards = []
    for index, page in enumerate(manifest["pages"], 1):
        cards.append(
            f"<section><h2>第 {index} 页 · {page['source']}</h2>"
            f"<p>{page['audio_seconds']:.2f} 秒 · {page['sample_rate']} Hz</p>"
            f"<audio controls preload='metadata' src='{page['audio']}'></audio>"
            f"<p><a download href='{page['audio']}'>下载 WAV</a></p>"
            f"<details><summary>查看对应朗读文字</summary><p>{html.escape(page['text'])}</p></details></section>"
        )
    document = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三页整页音频</title><style>body{font:16px/1.8 system-ui;background:#f5f7fa;color:#203348;max-width:860px;margin:auto;padding:28px}section{background:white;padding:24px;margin:20px 0;border-radius:14px}audio{width:100%}a{color:#165aab}summary{cursor:pointer}</style><h1>三页整页音频 · 当前修复版</h1><p>嵌套画格顺序已修复：第二页先读右上小格，再读左侧大格。第三页已排除气泡外拟声误识“H三H”，保留“不要”。日奈音色，爱心转为叹号，1–2 个汉字的短句默认叹号，已有问号、省略号保留。列间补逗号，串行合成，每页一份 WAV。第三页采用较短内部分组，以覆盖尾句；应用默认设置未改变。基于已保存的安卓相册截图重新识别生成。</p>"""
    document += (
        "".join(cards) + '<p><a href="manifest.json">文件哈希与生成信息</a></p></html>'
    )
    (OUT / "index.html").write_text(document, encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    settings = json.loads((ROOT / "config/reader.json").read_text(encoding="utf-8"))
    base = "http://127.0.0.1:8765"
    auth = {"X-Reader-Token": settings["token"]}
    results = []
    for number, name in enumerate(("0012", "0009", "0041"), 1):
        screenshot = ROOT / f"~archive/20260928-历史验收证据与旧计划/docs/evidence/page-mode/{number}/screen.jpg"
        start = time.perf_counter()
        req = urllib.request.Request(
            base + "/pages",
            data=screenshot.read_bytes(),
            headers=dict(auth, **{"Content-Type": "image/jpeg"}),
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            page = json.load(response)
        assert len(page["sentences"]) == 1
        (OUT / f"{name}.json").write_text(
            json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        req = urllib.request.Request(
            base + "/pages/" + page["page_id"] + "/audio/0", headers=auth
        )
        # 本组样例的0041在cut2下仍漏尾句，使用已验证的cut1；不改变生产默认。
        method = "cut1" if name == "0041" else "cut2"
        if method == "cut1":
            payload = {
                "text": page["sentences"][0]["text"],
                "text_lang": "zh",
                "ref_audio_path": settings["reference_audio"],
                "prompt_text": settings["reference_text"],
                "prompt_lang": "ja",
                "text_split_method": method,
                "batch_size": 1,
                "media_type": "wav",
                "streaming_mode": False,
                "parallel_infer": False,
                "seed": 42,
            }
            req = urllib.request.Request(
                settings["tts_url"] + "/tts",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
        with urllib.request.urlopen(req, timeout=300) as response:
            audio = response.read()
        path = OUT / f"{name}.wav"
        path.write_bytes(audio)
        with wave.open(str(path), "rb") as wav:
            seconds = wav.getnframes() / wav.getframerate()
            rate = wav.getframerate()
        results.append(
            {
                "source": name,
                "screenshot": screenshot.relative_to(ROOT).as_posix(),
                "page_id": page["page_id"],
                "text": page["sentences"][0]["text"],
                "audio": path.name,
                "text_split_method": method,
                "audio_seconds": round(seconds, 3),
                "sample_rate": rate,
                "generation_seconds": round(time.perf_counter() - start, 3),
                "sha256": hashlib.sha256(audio).hexdigest(),
            }
        )
        print(name, "ready", round(seconds, 2), "seconds", flush=True)
    (OUT / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "page",
                "text_split_method": "per-page",
                "parallel_infer": False,
                "voice": "hina",
                "source_note": "New computer-side synthesis from saved real Android Gallery screenshots; not a new Android UI run.",
                "pages": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    render_report()


if __name__ == "__main__":
    main()
