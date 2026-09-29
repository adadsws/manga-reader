import hashlib
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/download_blue_archive_hina.py"
SPEC = importlib.util.spec_from_file_location("download_blue_archive_hina", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class BlueArchiveHinaDownloadTests(unittest.TestCase):
    def make_archive(self, root: Path, unsafe: bool = False) -> Path:
        archive = root / "日奈.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            if unsafe:
                bundle.writestr("../escape.txt", b"unsafe")
            else:
                base = "日奈模型/"
                bundle.writestr(base + "日奈-e10.ckpt", b"gpt")
                bundle.writestr(base + "日奈_e10_s160_l32.pth", b"sovits")
                bundle.writestr(base + "reference.wav", b"audio")
                bundle.writestr(base + "角色头像.webp", b"image")
        return archive

    def test_source_is_one_pinned_character_archive(self):
        self.assertEqual(MODULE.SOURCE_PATH, "蔚蓝档案/日语/日奈.zip")
        self.assertEqual(
            MODULE.REVISION, "afe9e5fe34392e87739b9a8445ce5028c0d2ab65"
        )
        self.assertNotIn("**", MODULE.SOURCE_URL)
        self.assertIn(MODULE.REVISION, MODULE.SOURCE_URL)

    def test_download_validates_before_caching(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_archive(root)
            content = source.read_bytes()
            cache = root / "cache/model.zip"
            result = MODULE.download_archive(
                source.as_uri(), cache, len(content), hashlib.sha256(content).hexdigest()
            )
            self.assertEqual(result, cache)
            self.assertEqual(cache.read_bytes(), content)

    def test_install_normalizes_wrapper_and_writes_source_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            target = root / "models/v4/格黑娜/日奈"
            MODULE.install_archive(archive, target, root / "work", [])
            self.assertTrue((target / "日奈-e10.ckpt").is_file())
            self.assertTrue((target / "日奈_e10_s160_l32.pth").is_file())
            self.assertTrue((target / "reference.wav").is_file())
            self.assertTrue((target / "角色头像.webp").is_file())
            self.assertTrue((target / ".source.json").is_file())

    def test_existing_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_archive(root)
            target = root / "existing"
            target.mkdir()
            with self.assertRaises(FileExistsError):
                MODULE.install_archive(archive, target)

    def test_archive_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.make_archive(root, unsafe=True)
            with self.assertRaises(RuntimeError):
                MODULE.safe_extract(archive, root / "output")


if __name__ == "__main__":
    unittest.main()
