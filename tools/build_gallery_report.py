# 从同一轮安卓日志、截图和实际响应音频生成可离线打开的实测文档。
import hashlib
import html
import json
import re
import shutil
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "~outputs-intermediate/evidence/gallery-full-run"


def esc(s):
    return html.escape(str(s))


def main():
    run = json.loads((OUT / "run.json").read_text(encoding="utf-8"))
    assert len(run["page_ids"]) == 3 and run["duplicate_stopped"]
    log = (OUT / "android.log").read_text(encoding="utf-8")
    completed = [line.split()[1] for line in log.splitlines() if "本页完成" in line]
    recognized = [line.split()[1] for line in log.splitlines() if "识别 " in line]
    stopped = [line.split()[1] for line in log.splitlines() if "画面未变化" in line][0]
    pages = []
    for number, (source, key) in enumerate(
        zip(["0012", "0009", "0041"], run["page_ids"]), 1
    ):
        folder = OUT / source
        folder.mkdir(exist_ok=True)
        raw = ROOT / "~outputs-intermediate/pages"
        shutil.copy2(raw / f"{key}.jpg", folder / "screen.jpg")
        shutil.copy2(raw / f"{key}.json", folder / "sentences.json")
        sentences = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))
        audio = folder / "audio"
        audio.mkdir(exist_ok=True)
        params = None
        frames = []
        items = []
        for i, s in enumerate(sentences, 1):
            f = raw / key / f"{i:03}.wav"
            assert f.exists(), f
            dest = audio / f.name
            shutil.copy2(f, dest)
            with wave.open(str(dest), "rb") as w:
                current = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                assert params is None or params == current
                params = current
                duration = w.getnframes() / w.getframerate()
                frames.append(w.readframes(w.getnframes()))
            items.append(
                {
                    "index": i,
                    "text": s["text"],
                    "original": s.get("original", ""),
                    "seconds": round(duration, 3),
                    "audio": dest.relative_to(OUT).as_posix(),
                    "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                }
            )
        with wave.open(str(folder / "page.wav"), "wb") as w:
            w.setnchannels(params[0])
            w.setsampwidth(params[1])
            w.setframerate(params[2])
            w.writeframes(b"".join(frames))
        pages.append(
            {
                "source": source,
                "page_id": key,
                "count": len(sentences),
                "seconds": round(sum(x["seconds"] for x in items), 3),
                "sample_rate": params[2],
                "recognized_utc": recognized[number - 1],
                "completed_utc": completed[number - 1],
                "items": items,
            }
        )
    played = re.findall(r"I Reader\s*:\s*\d+/\d+ (.*)", log)
    expected = [item["text"] for page in pages for item in page["items"]]
    assert played == expected, "Playback log does not match saved sentence order"
    assert len(completed) == len(recognized) == 3
    manifest = {
        "run": run,
        "all_saved_sentences_matched_playback_log": True,
        "log_sha256": hashlib.sha256((OUT / "android.log").read_bytes()).hexdigest(),
        "gpt_sovits_upstream": "d523079fc05d9a8028d6085bffe4a2757c32abb6",
        "tts_parameters": {
            "voice": "hina",
            "version": "v4",
            "text_lang": "zh",
            "prompt_lang": "ja",
            "text_split_method": "cut5",
            "batch_size": 1,
            "seed": 42,
        },
        "pages": pages,
        "stopped_utc": stopped,
        "audio_provenance": "Actual WAV response bytes retained during this Android Gallery run. Page WAV is lossless PCM concatenation in sentence order; no re-synthesis or inserted silence.",
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    intro = "本报告记录 2026-09-19 在官方模拟器 AOSP Android 15（API 35 / x86_64）自带 Gallery 中新进行的一次三页连续实测。使用日奈单音色，安卓经局域网连接电脑，由电脑执行 OCR、分镜排序和 GPT-SoVITS 合成。与此前未留存音频的测试分开记录。GPT-SoVITS 固定版本 d523079fc05d9a8028d6085bffe4a2757c32abb6；v4 日奈模型，中文目标、日语参考，cut5 / batch_size=1 / seed=42。"
    intro += f" 本轮从点击读到末页停止共 {run['elapsed_seconds']:.2f} 秒。"
    steps = [
        "电脑启动 OCR 与 TTS 服务，临时开启 save_debug_pages，同时留存截图、识别结果和返回安卓的逐句 WAV。",
        "三张漫画已导入 /sdcard/Pictures/ReaderTest；打开系统相册 com.android.gallery3d，进入 0012 单图，等待工具栏自动隐藏。",
        "启用浮窗及翻页无障碍，系统投屏选择 Entire screen；开启自动翻页，向右滑动，高度 55%，时长 400 毫秒。",
        "将浮窗拖到右上对白上，点击一次“读”。之后由应用执行截图、上传、识别排序、逐句播放，播放完成才翻页。",
        "依次读取 0012 → 0009 → 0041；第三页结束继续向右滑动，画面相同，应用停止自动循环。",
        "留存原始日志、上传截图及实际 WAV，逐页按句序无损拼接整页音频。整页音频不含 OCR、合成等待与翻页空闲；不是模拟器扬声器回录。",
    ]
    limitations = "OCR 原文和误识别均保留，没有人工润色后重新合成。句数表示程序输出，不代表逐字准确率；小字、拟声词及符号存在误识别和遗漏，0041 没有完整人工逐字金标准。播放完成依据安卓 MediaPlayer 回调日志，未作主观音质评分。本报告不外推到实体手机或所有相册。"
    md = [
        "# 安卓系统相册三页全流程实测（含音频）",
        "",
        intro,
        "",
        "## 操作流程",
        "",
    ] + [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    md += [
        "",
        "## 运行结果",
        "",
        "| 页码 | 文件 | 句数 | 音频时长 | 识别完成 UTC | 本页播放完成 UTC | 整页音频 |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for i, p in enumerate(pages, 1):
        md.append(
            f"| {i} | {p['source']} | {p['count']} | {p['seconds']:.2f} 秒 | {p['recognized_utc']} | {p['completed_utc']} | [播放]({p['source']}/page.wav) |"
        )
    md += [
        "",
        f"末页重复停止：{stopped} UTC（北京时间为 UTC+8）。",
        "",
        limitations,
        "",
        "[浮窗遮挡前截图](overlay-before.png) · [末页停止截图](end.png) · [安卓原始日志](android.log) · [逐句时长和 SHA256](manifest.json) · [音频与截图完整性校验](verification.json) · [截图原图匹配](image-matches.json)",
        "",
    ]
    sections = []
    for i, p in enumerate(pages, 1):
        src = p["source"]
        md += [
            f"## 第 {i} 页：{src}",
            "",
            f"![实际上传截图]({src}/screen.jpg)",
            "",
            f"[整页音频]({src}/page.wav) · [OCR 原始结果]({src}/sentences.json)",
            "",
            "| 句序 | 实际送入语音的文字 | 时长 | 对应 WAV |",
            "|---:|---|---:|---|",
        ]
        rows = []
        for item in p["items"]:
            md.append(
                f"| {item['index']} | {item['text'].replace('|', '&#124;')} | {item['seconds']:.2f} 秒 | [播放]({item['audio']}) |"
            )
            rows.append(
                f"<tr><td>{item['index']}</td><td>{esc(item['text'])}</td><td>{item['seconds']:.2f} 秒</td><td><audio controls preload='none' src='{item['audio']}'></audio></td></tr>"
            )
        md.append("")
        sections.append(
            f"<section id='p{i}'><h2>第 {i} 页 · {src}</h2><p>{p['count']} 句 · {p['seconds']:.2f} 秒 · {p['sample_rate']} Hz</p><div class='page'><a href='{src}/screen.jpg'><img src='{src}/screen.jpg' alt='第{i}页实际上传截图'></a><div><h3>整页音频</h3><audio controls preload='metadata' src='{src}/page.wav'></audio><p>识别完成 {p['recognized_utc']} UTC<br>播放完成 {p['completed_utc']} UTC</p><p><a href='{src}/sentences.json'>OCR 原始结果</a> · <a download href='{src}/page.wav'>下载整页 WAV</a></p></div></div><details><summary>逐句文字与 {p['count']} 段对应音频</summary><table><thead><tr><th>句序</th><th>实际送入语音的文字</th><th>时长</th><th>对应音频</th></tr></thead><tbody>{''.join(rows)}</tbody></table></details></section>"
        )
    (OUT / "实测报告.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    css = "body{font:16px/1.7 system-ui,sans-serif;background:#f4f6fa;color:#17243a;margin:0}main{max-width:1100px;margin:auto;padding:32px}section,header{background:white;border-radius:14px;padding:24px;margin:22px 0}h1{font-size:30px}a{color:#185da9}nav a{margin-right:24px}.page{display:flex;gap:28px;flex-wrap:wrap}.page img{width:320px;max-width:100%}audio{max-width:100%}table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}summary{cursor:pointer;font-weight:600;margin:20px 0}.note{background:#eef3fa;padding:18px;border-radius:10px}@media(max-width:600px){main{padding:12px}section,header{padding:16px}td audio{width:150px}}"
    doc = f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>安卓系统相册三页全流程实测</title><style>{css}</style><main><header><h1>安卓系统相册三页全流程实测</h1><p>{intro}</p><nav><a href='#p1'>第1页</a><a href='#p2'>第2页</a><a href='#p3'>第3页</a><a href='实测报告.md'>Markdown 文档</a></nav></header><section><h2>操作流程</h2><ol>{''.join('<li>' + esc(x) + '</li>' for x in steps)}</ol><p>末页重复停止：{stopped} UTC；北京时间为 UTC+8。</p><p><a href='overlay-before.png'>浮窗遮挡前</a> · <a href='end.png'>末页停止</a> · <a href='android.log'>原始日志</a> · <a href='manifest.json'>时长与 SHA256</a> · <a href='verification.json'>音频与截图校验</a> · <a href='image-matches.json'>截图原图匹配</a></p><p class='note'>{limitations}</p></section>{''.join(sections)}</main></html>"
    (OUT / "index.html").write_text(doc, encoding="utf-8")
    print(
        json.dumps(
            [
                {"page": p["source"], "sentences": p["count"], "seconds": p["seconds"]}
                for p in pages
            ]
        )
    )


if __name__ == "__main__":
    main()
