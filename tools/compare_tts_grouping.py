# 固定同页文字、音色和参数，仅比较分组边界；角色分组由人工看图确认。
import hashlib
import html
import io
import json
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "~outputs-intermediate/evidence/tts-grouping"
OUT.mkdir(parents=True, exist_ok=True)
settings = json.loads((ROOT / "config/reader.json").read_text(encoding="utf-8"))
source = json.loads(
    (ROOT / "~archive/20260928-历史验收证据与旧计划/docs/evidence/gallery-full-run/0012/sentences.json").read_text(
        encoding="utf-8"
    )
)
texts = [
    s["text"] if s["text"][-1] in "。？！!?…" else s["text"] + "。" for s in source
]
strategies = [
    ("page", "整页合并", [list(range(1, 11))]),
    ("bubble", "完整气泡", [[1], [2, 3], [4], [5], [6, 7, 8], [9], [10]]),
    ("speaker", "同角色相邻气泡", [[1, 2, 3], [4, 5], [6, 7, 8], [9], [10]]),
]
params = {
    "text_lang": "zh",
    "ref_audio_path": settings["reference_audio"],
    "prompt_text": settings["reference_text"],
    "prompt_lang": "ja",
    "text_split_method": "cut0",
    "batch_size": 1,
    "media_type": "wav",
    "streaming_mode": False,
    "parallel_infer": True,
    "seed": 42,
    "fragment_interval": 0.3,
    "speed_factor": 1.0,
}


def synth(text):
    req = urllib.request.Request(
        settings["tts_url"] + "/tts",
        data=json.dumps(dict(params, text=text)).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=240) as r:
        data = r.read()
    with wave.open(io.BytesIO(data), "rb") as w:
        frames = w.readframes(w.getnframes())
        fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        duration = w.getnframes() / w.getframerate()
    return data, frames, fmt, duration, time.perf_counter() - start


# 各方案前共享一次预热，避免首个方案额外承担模型初次加载。
synth("开始语音对比测试。")
results = []
for key, label, groups in strategies:
    folder = OUT / key
    folder.mkdir(exist_ok=True)
    chunks = []
    items = []
    fmt = None
    for i, indices in enumerate(groups, 1):
        text = "".join(texts[j - 1] for j in indices)
        data, frames, current, duration, elapsed = synth(text)
        assert fmt is None or fmt == current
        fmt = current
        chunks.append(frames)
        (folder / f"{i:02}.wav").write_bytes(data)
        items.append(
            {
                "indices": indices,
                "text": text,
                "audio": f"{key}/{i:02}.wav",
                "audio_seconds": round(duration, 3),
                "request_seconds": round(elapsed, 3),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        print(key, i, round(elapsed, 2), flush=True)
    with wave.open(str(folder / "full.wav"), "wb") as w:
        w.setnchannels(fmt[0])
        w.setsampwidth(fmt[1])
        w.setframerate(fmt[2])
        w.writeframes(b"".join(chunks))
    results.append(
        {
            "key": key,
            "label": label,
            "groups": groups,
            "items": items,
            "requests": len(groups),
            "audio_seconds": round(sum(x["audio_seconds"] for x in items), 3),
            "total_request_seconds": round(sum(x["request_seconds"] for x in items), 3),
            "first_audio_seconds": items[0]["request_seconds"],
        }
    )
manifest = {
    "source": "../gallery-full-run/0012/sentences.json",
    "normalization": "Only add a final Chinese full stop to units 5, 9, 10; same normalized text in all strategies. No wording changes.",
    "annotation": "Manual visual grouping: 1-3 dark-haired girl; 4-5 light-haired girl; 6-8 dark-haired girl off-panel; 9 light-haired girl; 10 dark-haired girl thought in next panel. Do not merge thought with dialogue. Connected bubble lobes count as one complete bubble. These are experimental annotations, not automatic character detection.",
    "params": params,
    "gpt_sovits_commit": "d523079fc05d9a8028d6085bffe4a2757c32abb6",
    "scope": "Computer-side synthesis comparison; does not change Android app or replay its end-to-end flow. One page, one seed, one warm run; timings are not statistical benchmarks.",
    "results": results,
}
(OUT / "results.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
rows = []
sections = []
for r in results:
    rows.append(
        f"<tr><td>{r['label']}</td><td>{r['requests']}</td><td>{r['first_audio_seconds']:.2f}s</td><td>{r['total_request_seconds']:.2f}s</td><td>{r['audio_seconds']:.2f}s</td></tr>"
    )
    entries = "".join(
        f"<li>{html.escape(x['text'])} <a href='{x['audio']}'>单段音频</a></li>"
        for x in r["items"]
    )
    sections.append(
        f"<section><h2>{r['label']}</h2><audio controls preload='metadata' src='{r['key']}/full.wav'></audio><p><a href='{r['key']}/full.wav'>下载完整试听</a></p><details><summary>实际分组文字</summary><ol>{entries}</ol></details></section>"
    )
doc = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三种TTS合并方式试听</title><style>body{font:16px/1.7 system-ui;background:#f3f5f9;color:#18263a;max-width:1000px;margin:auto;padding:28px}section,header{background:white;border-radius:14px;padding:24px;margin:20px 0}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse;width:100%}audio{width:100%;max-width:600px}a{color:#165ca3}</style><header><h1>三种 TTS 合并方式试听</h1><p>同一页 0012、同一日奈音色、相同文字顺序、seed=42、cut0、batch_size=1、语速1.0。只改变分组边界。未使用换行切分；本页少于上游510字符长文本保护阈值。补齐缺失的句末句号，三组文字相同，未改写OCR词句。</p><p>“完整气泡”把相连气泡瓣视为一个气泡；“同角色相邻气泡”由人工看图核对，没有使用 hina 音色ID判断角色。当前应用未自动实现这些分组。</p><p><a href="../gallery-full-run/0012/screen.jpg">查看测试漫画</a> · <a href="results.json">参数、分组和WAV哈希</a></p></header>"""
doc += (
    "<section><h2>实际生成指标</h2><p>首段返回时间不是发声延迟；按完整 WAV 返回接口计时。合成总耗时为顺序请求之和，没有模拟安卓预取。单页、单次预热后测量，不是统计性能评测，也不代表主观音质评分。</p><table><tr><th>方式</th><th>请求数</th><th>首段返回</th><th>合成总耗时</th><th>音频时长</th></tr>"
    + "".join(rows)
    + "</table></section>"
    + "".join(sections)
)
doc += "<section><h2>如何选择</h2><p>人物归属可靠时，优先合并同角色连续发言：能减少独立合成的语气重置，同时保留人物切换。当前自动处理建议以完整气泡为默认，角色不确定时不跨气泡合并。整页方式适合单人旁白；多角色漫画会把人物切换也交给同一次合成，控制和校对粒度较粗。</p><p>这属于结构与交互上的推荐，不冒充已完成的盲听音质排名。试听时重点比较开头三句、道歉两句、连续提醒三句之间的衔接，以及换人处的停顿。OCR缺字和参考音色本身的问题不会因合并自动消失。</p></section></html>"
(OUT / "index.html").write_text(doc, encoding="utf-8")
print(
    json.dumps(
        [
            {
                k: r[k]
                for k in (
                    "label",
                    "requests",
                    "first_audio_seconds",
                    "total_request_seconds",
                    "audio_seconds",
                )
            }
            for r in results
        ],
        ensure_ascii=False,
    ),
    flush=True,
)
