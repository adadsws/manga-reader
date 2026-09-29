"""下载并校验固定版本的蔚蓝档案日奈 GPT-SoVITS 模型。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config/voice-model.lock.json"
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
REPOSITORY = LOCK["source"]["repository"]
REVISION = LOCK["source"]["revision"]
SOURCE_PATH = LOCK["source"]["path"]
ARCHIVE_BYTES = LOCK["source"]["bytes"]
ARCHIVE_SHA256 = LOCK["source"]["sha256"]
SOURCE_URL = (
    f"https://www.modelscope.cn/models/{REPOSITORY}/resolve/{REVISION}/"
    + urllib.parse.quote(SOURCE_PATH)
)
CACHE = ROOT / "~temp/downloads/modelscope-gpt-sovits" / "日奈.zip"
TARGET = ROOT / LOCK["directory"]
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(
    path: Path,
    expected_bytes: int = ARCHIVE_BYTES,
    expected_sha256: str = ARCHIVE_SHA256,
) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != expected_bytes:
        raise RuntimeError(
            f"日奈模型包大小错误：{path.stat().st_size}，应为 {expected_bytes}"
        )
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise RuntimeError(f"日奈模型包 SHA-256 错误：{actual}")


def download_archive(
    url: str = SOURCE_URL,
    cache: Path = CACHE,
    expected_bytes: int = ARCHIVE_BYTES,
    expected_sha256: str = ARCHIVE_SHA256,
) -> Path:
    if cache.exists():
        verify_archive(cache, expected_bytes, expected_sha256)
        print("已复用并校验下载缓存：", cache)
        return cache

    cache.parent.mkdir(parents=True, exist_ok=True)
    partial = cache.with_suffix(cache.suffix + ".download")
    if partial.exists():
        partial.unlink()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "manga-reader"})
        with urllib.request.urlopen(request, timeout=120) as response, partial.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
        verify_archive(partial, expected_bytes, expected_sha256)
        partial.replace(cache)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    print("已下载并校验日奈单角色模型包：", cache)
    return cache


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            relative = Path(item.filename.replace("\\", "/"))
            output = (destination / relative).resolve()
            if output != destination and destination not in output.parents:
                raise RuntimeError(f"模型包包含越界路径：{item.filename}")
            mode = (item.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise RuntimeError(f"模型包包含不允许的符号链接：{item.filename}")
            if item.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target)


def payload_root(extracted: Path) -> Path:
    current = extracted
    while True:
        children = list(current.iterdir())
        directories = [item for item in children if item.is_dir()]
        files = [item for item in children if item.is_file()]
        if files or len(directories) != 1:
            return current
        current = directories[0]


def validate_payload(payload: Path) -> list[Path]:
    files = sorted(item for item in payload.rglob("*") if item.is_file())
    suffixes = {item.suffix.lower() for item in files}
    missing = []
    if ".ckpt" not in suffixes:
        missing.append("GPT .ckpt")
    if ".pth" not in suffixes:
        missing.append("SoVITS .pth")
    if not suffixes.intersection(AUDIO_SUFFIXES):
        missing.append("参考音频")
    if not suffixes.intersection(IMAGE_SUFFIXES):
        missing.append("头像")
    if missing:
        raise RuntimeError("日奈模型包缺少：" + "、".join(missing))
    return files


def validate_locked_files(payload: Path, expected_files: list[dict]) -> None:
    for record in expected_files:
        matches = [item for item in payload.rglob(record["name"]) if item.is_file()]
        if len(matches) != 1:
            raise RuntimeError(f"日奈模型包中的 {record['name']} 数量不是 1")
        model_file = matches[0]
        if model_file.stat().st_size != record["bytes"]:
            raise RuntimeError(f"日奈模型文件大小错误：{record['name']}")
        if file_sha256(model_file) != record["sha256"]:
            raise RuntimeError(f"日奈模型文件 SHA-256 错误：{record['name']}")


def install_archive(
    archive: Path,
    target: Path = TARGET,
    work_root: Path | None = None,
    expected_files: list[dict] | None = None,
) -> Path:
    if target.exists():
        raise FileExistsError(
            f"目标已存在，未覆盖：{target}。请先人工确认并移动旧目录后再运行。"
        )
    work_root = work_root or ROOT / "~temp/model-downloads"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hina-", dir=work_root) as temporary:
        temporary_path = Path(temporary)
        extracted = temporary_path / "extracted"
        safe_extract(archive, extracted)
        payload = payload_root(extracted)
        files = validate_payload(payload)
        validate_locked_files(
            payload, LOCK["files"] if expected_files is None else expected_files
        )

        prepared = temporary_path / "prepared"
        shutil.copytree(payload, prepared)
        manifest = []
        for source in files:
            relative = source.relative_to(payload).as_posix()
            installed = prepared / relative
            manifest.append(
                {
                    "path": relative,
                    "bytes": installed.stat().st_size,
                    "sha256": file_sha256(installed),
                }
            )
        (prepared / ".source.json").write_text(
            json.dumps(
                {
                    "repository": REPOSITORY,
                    "revision": REVISION,
                    "source_path": SOURCE_PATH,
                    "archive_bytes": ARCHIVE_BYTES,
                    "archive_sha256": ARCHIVE_SHA256,
                    "files": manifest,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        prepared.replace(target)
    print("日奈模型已安装：", target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只下载并安装蔚蓝档案日语日奈 GPT-SoVITS 模型"
    )
    parser.add_argument(
        "--download-only", action="store_true", help="只下载和校验 ZIP，不解压安装"
    )
    options = parser.parse_args()
    if not options.download_only and TARGET.exists():
        raise FileExistsError(
            f"目标已存在，未下载或覆盖：{TARGET}。请先人工确认并移动旧目录后再运行。"
        )
    archive = download_archive()
    if not options.download_only:
        install_archive(archive)


if __name__ == "__main__":
    main()
