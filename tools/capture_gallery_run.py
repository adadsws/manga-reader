# 本轮系统相册证据收集，只旁观页面文件和日志，不注入翻页。
import json
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
adb = str(root / "~temp/android-sdk/platform-tools/adb.exe")
out = root / "~outputs-intermediate/evidence/gallery-full-run"
pages = root / "~outputs-intermediate/pages"


def call(*args):
    return subprocess.check_output([adb, *args])


def main():
    assert b"org.local.reader" in call(
        "shell", "settings", "get", "secure", "enabled_accessibility_services"
    ), "Enable the Android accessibility service through system UI first"
    out.mkdir(parents=True, exist_ok=True)
    before = set(pages.glob("*.json"))
    seen = []
    call("logcat", "-c")
    (out / "overlay-before.png").write_bytes(call("exec-out", "screencap", "-p"))
    (out / "device.txt").write_bytes(call("shell", "getprop"))
    (out / "foreground.txt").write_bytes(call("shell", "dumpsys", "window"))
    call("shell", "input", "tap", "580", "465")
    start = time.time()
    while time.time() - start < 360:
        for p in sorted(
            set(pages.glob("*.json")) - before, key=lambda p: p.stat().st_mtime
        ):
            if p.stem not in seen:
                seen.append(p.stem)
                print("page", len(seen), p.stem, flush=True)
        log = call("logcat", "-d", "-s", "Reader:I", "*:S")
        (out / "android.log").write_bytes(log)
        if "画面未变化".encode() in log:
            (out / "end.png").write_bytes(call("exec-out", "screencap", "-p"))
            break
        time.sleep(2)
    (out / "run.json").write_text(
        json.dumps(
            {
                "page_ids": seen,
                "elapsed_seconds": round(time.time() - start, 2),
                "duplicate_stopped": "画面未变化".encode() in log,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("finished", seen, flush=True)


if __name__ == "__main__":
    main()
