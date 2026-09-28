# 使用固定官方 SDK 构建，无额外 Gradle/Maven 依赖。
import argparse
from pathlib import Path
import subprocess
import zipfile
import os
import json
import shutil
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "~temp/android-sdk"
BT = SDK / "build-tools/35.0.0"
JAVA = (
    Path(json.loads((ROOT / "config/runtime.json").read_text(encoding="utf-8"))["java"])
    / "bin"
)
ANDROID = SDK / "platforms/android-35/android.jar"
GENERATED_APK_DIR = ROOT / "~outputs-final/android"
PUBLISHED_APK = ROOT / "reader.apk"
os.environ["JAVA_HOME"] = str(JAVA.parent)


def run(args, cwd=None):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def output_path(name):
    """正式应用固定覆盖发布文件；测试 APK 仍写入可重建输出目录。"""
    return PUBLISHED_APK if name == "reader" else GENERATED_APK_DIR / (name + ".apk")


def build(folder, name, assets=None, classpath=None, asset_names=None):
    src = ROOT / folder
    # 每次使用新目录，避免已归档 Java 类残留在旧 classes 中重新打包。
    work = ROOT / "~temp/build" / name / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work.mkdir(parents=True, exist_ok=True)
    gen = work / "gen"
    gen.mkdir(exist_ok=True)
    classes = work / "classes"
    classes.mkdir(exist_ok=True)
    dex = work / "dex"
    dex.mkdir(exist_ok=True)
    args = [
        BT / "aapt.exe",
        "package",
        "-f",
        "-M",
        src / "AndroidManifest.xml",
        "-I",
        ANDROID,
        "-J",
        gen,
        "-F",
        work / "resources.apk",
    ]
    if (src / "res").exists():
        args.extend(["-S", src / "res"])
    if assets:
        asset_root = (ROOT / assets).resolve()
        packaged_assets = asset_root
        if asset_names:
            # fixture 只暂存本次声明的资源，避免把同目录的完整测试书库打进 APK。
            packaged_assets = work / "assets"
            packaged_assets.mkdir()
            for name_in_assets in asset_names:
                source = (asset_root / name_in_assets).resolve()
                if asset_root not in source.parents or not source.is_file():
                    raise ValueError(f"无效 Android asset：{name_in_assets}")
                target = packaged_assets / name_in_assets
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        args.extend(["-A", packaged_assets])
    run(args)
    sources = list((src / "src").rglob("*.java")) + list(gen.rglob("*.java"))
    run(
        [
            JAVA / "javac.exe",
            "-encoding",
            "UTF-8",
            "--release",
            "8",
            "-classpath",
            os.pathsep.join(str(p) for p in [ANDROID, *(classpath or [])]),
            "-d",
            classes,
        ]
        + sources
    )
    run([JAVA / "jar.exe", "cf", work / "classes.jar", "-C", classes, "."])
    run(
        [
            BT / "d8.bat",
            "--lib",
            ANDROID,
            "--min-api",
            "26",
            "--output",
            dex,
            work / "classes.jar",
        ]
    )
    with zipfile.ZipFile(work / "resources.apk", "a") as z:
        z.write(dex / "classes.dex", "classes.dex")
    run([BT / "zipalign.exe", "-f", "4", work / "resources.apk", work / "aligned.apk"])
    key = ROOT / "~temp/build/debug.keystore"
    if not key.exists():
        run(
            [
                JAVA / "keytool.exe",
                "-genkeypair",
                "-keystore",
                key,
                "-storepass",
                "android",
                "-keypass",
                "android",
                "-alias",
                "androiddebugkey",
                "-dname",
                "CN=Android Debug,O=Local Reader,C=CN",
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "10000",
            ]
        )
    apk = output_path(name)
    apk.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            BT / "apksigner.bat",
            "sign",
            "--ks",
            key,
            "--ks-pass",
            "pass:android",
            "--v4-signing-enabled",
            "false",
            "--out",
            apk,
            work / "aligned.apk",
        ]
    )
    run([BT / "apksigner.bat", "verify", apk])
    print(apk)
    return work


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="另外构建需要本地 secrets/manga 三页素材的 reader-fixture.apk",
    )
    options = parser.parse_args()
    build("android", "reader")
    if options.fixture:
        build(
            "android-fixture",
            "reader-fixture",
            "secrets/manga",
            asset_names=("0009.jpg", "0012.jpg", "0041.jpg"),
        )
