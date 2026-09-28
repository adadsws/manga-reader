# 参考 tavern 的运行视图装配方式：固定源码、只读权重、独立状态。
# tavern e6bce3df5704ff2c3dbba51a791a738857a9a177 apps/gpt-sovits/launcher/run_view.py
from pathlib import Path
import shutil
import os
import json

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    settings = json.loads((ROOT / "config/runtime.json").read_text(encoding="utf-8"))
    source = ROOT / "reference/tts/GPT-SoVITS"
    view = ROOT / "~temp/gpt-sovits"
    pre = Path(settings["pretrained"])
    if not pre.is_dir():
        raise FileNotFoundError("请在 config/runtime.json 配置 GPT-SoVITS 基础模型目录")
    shutil.copytree(source, view, dirs_exist_ok=True)

    def link(a, b):
        if Path(b).exists():
            return b
        try:
            os.link(a, b)
        except OSError:
            shutil.copy2(a, b)
        return b

    shutil.copytree(
        pre,
        view / "GPT_SoVITS/pretrained_models",
        copy_function=link,
        dirs_exist_ok=True,
    )
    for name in ["G2PWModel", "ja_userdic"]:
        src = pre / "text" / name
        if src.exists():
            shutil.copytree(src, view / "GPT_SoVITS/text" / name, dirs_exist_ok=True)
    return view


if __name__ == "__main__":
    print(prepare())
