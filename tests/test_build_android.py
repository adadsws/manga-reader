# Android 构建路径回归：正式 APK 唯一发布，测试 APK 保持可重建输出。
import subprocess
import sys
import unittest

from tools import build_android


class AndroidBuildPathTests(unittest.TestCase):
    def test_reader_uses_the_single_published_apk(self):
        self.assertEqual(build_android.output_path("reader"), build_android.ROOT / "reader.apk")

    def test_non_release_apks_stay_in_generated_outputs(self):
        self.assertEqual(
            build_android.output_path("reader-fixture"),
            build_android.ROOT / "~outputs-final/android/reader-fixture.apk",
        )
        self.assertEqual(
            build_android.output_path("reader-tests"),
            build_android.ROOT / "~outputs-final/android/reader-tests.apk",
        )

    def test_help_documents_explicit_fixture_build(self):
        result = subprocess.run(
            [sys.executable, str(build_android.ROOT / "tools/build_android.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertIn("--fixture", result.stdout)
