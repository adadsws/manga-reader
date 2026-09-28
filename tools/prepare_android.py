# Android SDK official immutable archives; validate before extraction.
import concurrent.futures
import hashlib
import json
import urllib.request
import zipfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "~temp/android-sdk"
ARCHIVES = [
    (
        "platform-tools_r37.0.1-win.zip",
        "e03e78b1d80b396f1c3358e31251cb31740e1110",
        "",
        None,
    ),
    (
        "emulator-windows_x64-16349944.zip",
        "99e809fc3e5e13bd5e552de24c7de79c6f911027",
        "",
        None,
    ),
    (
        "platform-35_r02.zip",
        "0bb560a90a7a2cbd0dd8348224d518b638fe7949",
        "platforms/android-35",
        None,
    ),
    (
        "build-tools_r35_windows.zip",
        "af059bb67cf7786f45ee0db85e2d24985df1b4b6",
        "build-tools/35.0.0",
        None,
    ),
    (
        "commandlinetools-win-16111833_latest.zip",
        "57d04f2d75eb8e8fffc5000a987e5de4b5a63e9d",
        "cmdline-tools/latest",
        None,
    ),
    (
        "x86_64-35_r02.zip",
        "2d857d170c0d1b827149565da34b3383e5306f7f",
        "system-images/android-35/default/x86_64",
        "sys-img/android/",
    ),
]


def prepare(item):
    name, digest, dest, prefix = item
    url = "https://dl.google.com/android/repository/" + (prefix or "") + name
    cache = ROOT / "~temp/downloads" / name
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        print("download", name, flush=True)
        with (
            urllib.request.urlopen(url, timeout=120) as response,
            cache.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
    h = hashlib.sha1()
    with cache.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    actual = h.hexdigest()
    if actual != digest:
        raise ValueError(f"checksum mismatch: {name}")
    target = SDK / dest
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cache) as z:
        names = z.namelist()
        root = names[0].split("/")[0] + "/"
        for info in z.infolist():
            relative = info.filename[len(root) :] if dest else info.filename
            if not relative:
                continue
            output = target / relative
            if not output.resolve().is_relative_to(target.resolve()):
                raise ValueError("unsafe archive path")
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, output.open("wb") as out:
                    shutil.copyfileobj(src, out)
    print("ready", name, flush=True)
    return {"url": url, "sha1": digest, "destination": str(target.relative_to(ROOT))}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(prepare, ARCHIVES))
    (ROOT / "config").mkdir(exist_ok=True)
    (ROOT / "config/android-sdk.lock.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
