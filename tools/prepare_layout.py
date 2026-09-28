"""下载固定的 Magi 权重；已有文件校验通过后直接复用。"""

import hashlib
import json
import urllib.request
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / "config/layout-model.lock.json").read_text(encoding="utf-8"))
target = ROOT / "models/layout/magiv2" / lock["filename"]
target.parent.mkdir(parents=True, exist_ok=True)


def valid(path):
    if not path.exists() or path.stat().st_size != lock["bytes"]:
        return False
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest() == lock["sha256"]


if not valid(target):
    part = target.with_suffix(".download")
    with (
        urllib.request.urlopen(lock["url"], timeout=120) as response,
        part.open("wb") as out,
    ):
        shutil.copyfileobj(response, out)
    if not valid(part):
        raise RuntimeError("Magi 下载校验失败，未替换原文件")
    part.replace(target)
print("Magi weights verified", target)
