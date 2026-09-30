import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.history import HistoryStore, canonical_json, compound_key, digest_bytes
from server.app import app, ensure_audio, settings, store


class HistoryStoreTests(unittest.TestCase):
    def make_store(self, root):
        return HistoryStore(Path(root) / "history")

    def test_latest_visible_version_wins_and_hidden_version_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            history = self.make_store(temp)
            try:
                first = history.register_page(
                    input_kind="image", input_sha="input", image_key="same",
                    ocr_key="ocr", text_key="text", sentences=[{"text": "第一版"}],
                    hidden=False, input_body=b"image",
                )
                second = history.register_page(
                    input_kind="image", input_sha="input", image_key="same",
                    ocr_key="ocr", text_key="text", sentences=[{"text": "第二版"}],
                    hidden=False, input_body=b"image",
                )
                for stage, key in (("image", "same"), ("ocr", "ocr"), ("text", "text")):
                    self.assertEqual(history.lookup_page(stage, key)["version_id"], second)
                third = history.register_page(
                    input_kind="image", input_sha="input", image_key="same",
                    ocr_key="ocr", text_key="text", sentences=[{"text": "第三版"}],
                    hidden=True, input_body=b"image",
                )
                for stage, key in (("image", "same"), ("ocr", "ocr"), ("text", "text")):
                    self.assertEqual(history.lookup_page(stage, key)["version_id"], second)
                history.flush()
                versions = history.list_versions()["pages"]
                self.assertEqual({row["id"] for row in versions}, {first, second, third})
                self.assertEqual(
                    {row["id"]: row["hidden"] for row in versions},
                    {first: 0, second: 0, third: 1},
                )
            finally:
                history.close()

    def test_restart_loads_active_content_and_deduplicates_blobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "history"
            history = HistoryStore(root)
            version = history.register_page(
                input_kind="image", input_sha=digest_bytes(b"same"), image_key="image",
                ocr_key="ocr", text_key="text", sentences=[{"text": "内容"}],
                input_body=b"same",
            )
            latest = history.register_page(
                input_kind="image", input_sha=digest_bytes(b"same"), image_key="image",
                ocr_key="ocr", text_key="text",
                sentences=[{"text": "内容"}], input_body=b"same",
            )
            history.close()
            reopened = HistoryStore(root)
            try:
                self.assertNotEqual(latest, version)
                self.assertEqual(reopened.lookup_page("image", "image")["version_id"], latest)
                self.assertEqual(reopened.lookup_page("image", "image")["sentences"][0]["text"], "内容")
                self.assertEqual(len(list((root / "blobs/input").rglob("*.jpg"))), 1)
                self.assertEqual(len(list((root / "blobs/json").rglob("*.json"))), 1)
            finally:
                reopened.close()

    def test_all_hidden_page_versions_are_history_misses(self):
        with tempfile.TemporaryDirectory() as temp:
            history = self.make_store(temp)
            try:
                version = history.register_page(
                    input_kind="image", input_sha="input", image_key="same",
                    ocr_key="ocr", text_key="text", sentences=[{"text": "仅留档"}],
                    hidden=True, input_body=b"image",
                )
                self.assertIsNone(history.lookup_page("image", "same"))
                history.flush()
                self.assertEqual(history.list_versions()["pages"][0]["id"], version)
                self.assertEqual(history.list_versions()["pages"][0]["hidden"], 1)
            finally:
                history.close()

    def test_audio_is_exactly_keyed_and_corruption_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            history = self.make_store(temp)
            try:
                key = compound_key("文字", "模型", 0.9, True)
                version = history.register_audio(key, b"RIFF-a", [{"verified": True}])
                self.assertEqual(history.lookup_audio(key)["body"], b"RIFF-a")
                history.flush()
                record = history.audio_index[key]
                path = history._blob_path("audio", record["audio_sha"], ".wav")
                path.write_bytes(b"broken")
                self.assertIsNone(history.lookup_audio(key))
                self.assertTrue(history.errors)
                self.assertIsNone(history.lookup_audio("different"))
                self.assertTrue(version)
            finally:
                history.close()

    def test_latest_visible_audio_wins_and_hidden_audio_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "history"
            history = HistoryStore(root)
            key = compound_key("文字", "模型", 0.9, True)
            first = history.register_audio(key, b"RIFF-first", [{"version": 1}])
            second = history.register_audio(key, b"RIFF-second", [{"version": 2}])
            hidden = history.register_audio(
                key, b"RIFF-hidden", [{"version": 3}], hidden=True
            )
            self.assertEqual(history.lookup_audio(key)["version_id"], second)
            history.close()
            reopened = HistoryStore(root)
            try:
                self.assertEqual(reopened.lookup_audio(key)["version_id"], second)
                versions = reopened.list_versions()["audio"]
                self.assertEqual(
                    {row["id"]: row["hidden"] for row in versions},
                    {first: 0, second: 0, hidden: 1},
                )
            finally:
                reopened.close()

    def test_v1_database_migrates_existing_records_as_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "history"
            root.mkdir(parents=True)
            sentences = [{"text": "旧记录"}]
            body = canonical_json(sentences).encode("utf-8")
            sentences_sha = digest_bytes(body)
            blob = root / "blobs" / "json" / sentences_sha[:2] / (sentences_sha + ".json")
            blob.parent.mkdir(parents=True)
            blob.write_bytes(body)
            db = sqlite3.connect(root / "history.sqlite3")
            db.execute(
                """CREATE TABLE page_versions(
                   id TEXT PRIMARY KEY, created REAL NOT NULL, input_kind TEXT NOT NULL,
                   input_sha TEXT, image_key TEXT, ocr_key TEXT, text_key TEXT,
                   sentences_sha TEXT NOT NULL, source_version TEXT, match_stage TEXT NOT NULL,
                   promote INTEGER NOT NULL, status TEXT NOT NULL)"""
            )
            db.execute(
                "INSERT INTO page_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("legacy", 1.0, "image", "input", "same", "ocr", "text",
                 sentences_sha, None, "none", 0, "complete"),
            )
            db.commit()
            db.close()
            history = HistoryStore(root)
            try:
                self.assertEqual(history.lookup_page("image", "same")["version_id"], "legacy")
                self.assertEqual(history.list_versions()["pages"][0]["hidden"], 0)
                migrated = sqlite3.connect(root / "history.sqlite3")
                try:
                    self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0], 2)
                finally:
                    migrated.close()
            finally:
                history.close()


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.auth = {"X-Reader-Token": settings["token"]}
        self.temp = tempfile.TemporaryDirectory()
        self.history = HistoryStore(Path(self.temp.name) / "history")
        self.history_patch = patch("server.app.history", self.history)
        self.history_patch.start()

    def tearDown(self):
        self.history_patch.stop()
        self.history.close()
        self.temp.cleanup()
        store.clear()

    @staticmethod
    def row(box=None):
        return {
            "original": "测试。", "text": "测试。", "box": box or [1, 1, 20, 20],
            "columns": [], "confidence": 1.0, "speaker_id": "default",
            "panel_id": 0, "panel_box": [0, 0, 100, 100],
        }

    def test_first_request_misses_then_image_hit_skips_ocr(self):
        class OCR:
            last_timings = {}
            def __init__(self): self.calls = 0
            def read(self, body, parallel=False):
                self.calls += 1
                return [HistoryApiTests.row()]
        fake = OCR()
        headers = {**self.auth, "X-Reader-Parallel-Vision": "off"}
        with patch("server.app.ocr", fake):
            first = self.client.post("/pages", headers=headers, content=b"same-image")
            second = self.client.post("/pages", headers=headers, content=b"same-image")
        self.assertEqual(first.json()["match_stage"], "none")
        self.assertEqual(second.json()["match_stage"], "image")
        self.assertEqual(fake.calls, 1)

    def test_different_images_can_match_ocr_then_reading_text(self):
        class OCR:
            last_timings = {}
            def __init__(self): self.box = [1, 1, 20, 20]
            def read(self, body, parallel=False): return [HistoryApiTests.row(list(self.box))]
        fake = OCR()
        headers = {**self.auth, "X-Reader-Parallel-Vision": "off"}
        with patch("server.app.ocr", fake):
            self.assertEqual(self.client.post("/pages", headers=headers, content=b"one").json()["match_stage"], "none")
            self.assertEqual(self.client.post("/pages", headers=headers, content=b"two").json()["match_stage"], "ocr")
            fake.box = [40, 40, 80, 80]
            self.assertEqual(self.client.post("/pages", headers=headers, content=b"three").json()["match_stage"], "text")

    def test_reuse_off_saves_latest_visible_while_hidden_result_is_skipped(self):
        class OCR:
            last_timings = {}
            def __init__(self): self.calls = 0
            def read(self, body, parallel=False):
                self.calls += 1
                row = HistoryApiTests.row()
                row["text"] = row["original"] = f"第{self.calls}版。"
                return [row]
        fake = OCR()
        base = {**self.auth, "X-Reader-Parallel-Vision": "off"}
        forced = {
            **base, "X-Reader-History-Reuse": "off",
            "X-Reader-History-Promote": "on",
        }
        hidden = {**forced, "X-Reader-History-Hidden": "on"}
        with patch("server.app.ocr", fake):
            first = self.client.post("/pages", headers=base, content=b"same").json()
            latest = self.client.post("/pages", headers=forced, content=b"same").json()
            reused_latest = self.client.post("/pages", headers=base, content=b"same").json()
            hidden_result = self.client.post("/pages", headers=hidden, content=b"same").json()
            after_hidden = self.client.post("/pages", headers=base, content=b"same").json()
        self.assertEqual(latest["match_stage"], "disabled")
        self.assertNotEqual(latest["sentences"], first["sentences"])
        self.assertEqual(reused_latest["sentences"], latest["sentences"])
        self.assertNotEqual(hidden_result["sentences"], latest["sentences"])
        self.assertEqual(after_hidden["sentences"], latest["sentences"])
        self.assertEqual(fake.calls, 3)

    def test_reading_text_hit_reuses_saved_audio(self):
        headers = {**self.auth, "X-Reader-ASR-Check": "off", "X-Reader-Speech-Speed": "1.00"}
        first = self.client.post("/pages/text", headers=headers, json={"text": "相同台词。"}).json()
        with patch("server.app.synthesize", return_value=b"RIFF-history") as synth:
            response = self.client.get(f"/pages/{first['page_id']}/audio/0", headers=headers)
            self.assertEqual(response.content, b"RIFF-history")
            self.assertEqual(synth.call_count, 1)
        second = self.client.post("/pages/text", headers=headers, json={"text": "相同台词。"}).json()
        self.assertEqual(second["match_stage"], "text")
        with patch("server.app.synthesize") as synth:
            response = self.client.get(f"/pages/{second['page_id']}/audio/0", headers=headers)
            self.assertEqual(response.content, b"RIFF-history")
            synth.assert_not_called()

    def test_concurrent_pages_with_same_text_synthesize_only_once(self):
        sentence = [{"text": "并发复用台词。", "speaker_id": "hina"}]
        first = store.add(sentence, history_reuse=True, history_hidden=False)
        second = store.add(sentence, history_reuse=True, history_hidden=False)

        def slow_synthesize(*args, **kwargs):
            time.sleep(0.1)
            return b"RIFF-concurrent"

        async def fetch_both():
            # One event loop mirrors the ASGI server. Separate TestClient worker
            # loops cannot safely contend for the application's asyncio lock.
            with patch("server.app.tts_lock", asyncio.Lock()):
                return await asyncio.gather(
                    ensure_audio(first, 0, False, 1.0),
                    ensure_audio(second, 0, False, 1.0),
                )

        with patch("server.app.synthesize", side_effect=slow_synthesize) as synth:
            responses = asyncio.run(fetch_both())
        self.assertEqual(responses, [b"RIFF-concurrent"] * 2)
        self.assertEqual(synth.call_count, 1)

    def test_asr_and_speed_are_part_of_cross_page_audio_key(self):
        sentence = [{"text": "参数隔离台词。", "speaker_id": "hina"}]

        async def generate(page_id, asr_check, speed):
            with patch("server.app.tts_lock", asyncio.Lock()):
                return await ensure_audio(page_id, 0, asr_check, speed)

        with patch("server.app.synthesize", side_effect=(b"RIFF-a", b"RIFF-b", b"RIFF-c")) as synth:
            outputs = []
            for asr_check, speed in ((False, 1.0), (False, 1.2), (True, 1.0)):
                page_id = store.add(sentence, history_reuse=True, history_hidden=False)
                outputs.append(asyncio.run(generate(page_id, asr_check, speed)))
            repeated = store.add(sentence, history_reuse=True, history_hidden=False)
            outputs.append(asyncio.run(generate(repeated, False, 1.0)))
        self.assertEqual(outputs, [b"RIFF-a", b"RIFF-b", b"RIFF-c", b"RIFF-a"])
        self.assertEqual(synth.call_count, 3)

    def test_history_query_requires_auth(self):
        self.assertEqual(self.client.get("/history").status_code, 401)
        self.assertEqual(self.client.get("/history", headers=self.auth).status_code, 200)


if __name__ == "__main__":
    unittest.main()
