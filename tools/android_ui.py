# 专用模拟器的结构化 UI 调试；按实际无障碍节点定位。
import subprocess
import xml.etree.ElementTree as E
import re
import sys
from pathlib import Path

ADB = Path(__file__).resolve().parents[1] / "~temp/android-sdk/platform-tools/adb.exe"


def adb(*args):
    return subprocess.check_output([str(ADB), "-s", "emulator-5554", *args]).decode(
        "utf-8", errors="replace"
    )


def nodes():
    adb("shell", "uiautomator", "dump", "/sdcard/reader-ui.xml")
    return list(E.fromstring(adb("shell", "cat", "/sdcard/reader-ui.xml")).iter("node"))


def tap(text):
    for n in nodes():
        if n.get("text") == text:
            x, y, r, b = map(int, re.findall(r"\d+", n.get("bounds")))
            adb("shell", "input", "tap", str((x + r) // 2), str((y + b) // 2))
            print("tapped", text)
            return
    raise RuntimeError("UI node not found: " + text)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        tap(sys.argv[1])
    else:
        for n in nodes():
            if n.get("text"):
                print(n.get("text"), n.get("bounds"))
