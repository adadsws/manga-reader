# 在专用 ReaderAosp35 上验证正式设置页和运行中浮窗透明度；不读取漫画内容。
import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from PIL import Image, ImageChops

from tools.android_ui import ADB, adb, nodes, tap
from tools.verify_overlay import start_overlay


ROOT = Path(__file__).resolve().parents[1]


def open_settings():
    adb("shell", "am", "start", "-W", "-n", "org.local.reader/.MainActivity")
    time.sleep(0.7)


def seekbar(prefix):
    for node in nodes():
        if node.get("class") == "android.widget.SeekBar" and node.get("content-desc", "").startswith(prefix):
            return node
    raise RuntimeError("未找到设置：" + prefix)


def opacity_value(node):
    match = re.search(r"(\d+)%$", node.get("content-desc", ""))
    if not match:
        raise RuntimeError("透明度无障碍值不可读")
    return int(match.group(1))


def set_opacity(percent):
    node = seekbar("浮窗透明度：")
    left, top, right, bottom = map(int, re.findall(r"\d+", node.get("bounds")))
    # 使用滑轨内侧坐标触发真实用户变更，避免点击中心先把进度重置为中值。
    inset = 24
    ratio = (percent - 30) / 70.0
    x = round(left + inset + (right - left - inset * 2) * ratio)
    adb("shell", "input", "tap", str(x), str((top + bottom) // 2))
    time.sleep(0.4)
    return opacity_value(seekbar("浮窗透明度："))


def screenshot(target):
    target.write_bytes(
        subprocess.check_output([str(ADB), "-s", "emulator-5554", "exec-out", "screencap", "-p"])
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="~outputs-intermediate/evidence/speech-speed-opacity-20260922")
    args = parser.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    if adb("emu", "avd", "name").splitlines()[0] != "ReaderAosp35":
        raise RuntimeError("只能在专用 ReaderAosp35 上验证")
    open_settings()
    initial_speed = seekbar("朗读速度：").get("content-desc")
    initial_opacity_node = seekbar("浮窗透明度：")
    initial_opacity = initial_opacity_node.get("content-desc")
    initial_opacity_value = opacity_value(initial_opacity_node)
    screenshot(out / "settings.png")
    results = {
        "device": "ReaderAosp35",
        "formal_app": "org.local.reader",
        "speed_setting": initial_speed,
        "opacity_setting": initial_opacity,
        "media_projection": False,
    }
    try:
        adb("shell", "input", "swipe", "540", "1700", "540", "500", "450")
        time.sleep(0.4)
        start_overlay()
        results["media_projection"] = True
        screenshot(out / "overlay-initial.png")
        open_settings()
        set_opacity(30)
        results["minimum"] = seekbar("浮窗透明度：").get("content-desc")
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        time.sleep(0.4)
        screenshot(out / "overlay-30.png")
        open_settings()
        set_opacity(100)
        results["maximum"] = seekbar("浮窗透明度：").get("content-desc")
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        time.sleep(0.4)
        screenshot(out / "overlay-100.png")
        difference = ImageChops.difference(
            Image.open(out / "overlay-30.png").convert("RGB"),
            Image.open(out / "overlay-100.png").convert("RGB"),
        )
        results["visual_difference_bbox"] = difference.getbbox()
        if results["visual_difference_bbox"] is None:
            raise RuntimeError("30% 与 100% 浮窗截图没有视觉差异")
        open_settings()
        set_opacity(initial_opacity_value)
        results["restored"] = seekbar("浮窗透明度：").get("content-desc")
        if not results["minimum"].endswith("30%") or not results["maximum"].endswith("100%"):
            raise RuntimeError("浮窗透明度未覆盖 30% 到 100%")
    finally:
        open_settings()
        adb("shell", "input", "swipe", "540", "1700", "540", "500", "450")
        time.sleep(0.4)
        try:
            tap("停止服务")
        except RuntimeError:
            adb("shell", "am", "stopservice", "-n", "org.local.reader/.ReaderService")
    (out / "android-settings.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False))
