import unittest
import asyncio
import json
import io
import wave
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from server.app import (
    app, settings, store, synthesize, warmup_state, ensure_audio, audio_tasks,
    page_prefetch_tasks, prefetch_page_audio, select_prefetch_mode,
    READER_PROTOCOL_VERSION, observe_android_version,
)


class ReaderApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # 非历史专项测试强制走原流水线，避免本机持久历史影响调用次数断言。
        self.auth = {
            "X-Reader-Token": settings["token"],
            "X-Reader-History-Reuse": "off",
        }

    def test_debug_audio_matches_response_and_is_opt_in(self):
        # 保存的是实际响应字节，关闭调试时不写文件。
        payload = b"RIFFxxxxWAVEtest"
        for enabled in (False, True):
            key = store.add([{"text": "测试"}])
            with tempfile.TemporaryDirectory() as temp:
                with (
                    patch("server.app.ROOT", Path(temp)),
                    patch.dict(settings, {"save_debug_pages": enabled}),
                    patch("server.app.synthesize", return_value=payload),
                ):
                    response = self.client.get(
                        "/pages/" + key + "/audio/0", headers=self.auth
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, payload)
                    target = (
                        Path(temp) / "~outputs-intermediate/pages" / key / "001.wav"
                    )
                    self.assertEqual(target.exists(), enabled)
                    if enabled:
                        self.assertEqual(target.read_bytes(), response.content)
            store.cancel(key)

    def test_tts_converts_hearts_preserving_other_symbols_and_long_groups(self):
        text = "真的？！嗯……♡。下一句。"
        with patch("server.app.urllib.request.urlopen") as request:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setparams((1, 2, 32000, 0, "NONE", "not compressed"))
                w.writeframes(b"\x00\x00" * 320)
            body = buf.getvalue()
            request.return_value.__enter__.return_value.read.return_value = body
            self.assertEqual(synthesize(text, asr_check=False), body)
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["text"], "真的？！嗯...！。下一句。")
            self.assertEqual(payload["text_split_method"], settings["text_split_method"])
            self.assertEqual(payload["prompt_lang"], settings["prompt_lang"])
            self.assertEqual(payload["top_k"], settings["top_k"])
            self.assertEqual(payload["repetition_penalty"], settings["repetition_penalty"])
            self.assertEqual(payload["parallel_infer"], settings["tts_parallel_infer"])
            self.assertEqual(payload["batch_size"], settings["tts_batch_size"])
            self.assertEqual(payload["speed_factor"], 1.0)

    def test_native_mode_keeps_long_page_and_forwards_cut(self):
        # 原生模式须把超过24字的完整页交给指定上游切法。
        text = "今天沿着河边走过小桥以后继续前往车站，接下来还要去图书馆归还借来的书。"
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setparams((1, 2, 32000, 0, "NONE", "not compressed"))
            w.writeframes(b"\x00\x00" * 320)
        for method in ("cut0", "cut1", "cut3"):
            with patch.dict(settings, {"speech_mode": "native", "text_split_method": method, "prompt_lang": "zh"}), patch("server.app.urllib.request.urlopen") as request:
                request.return_value.__enter__.return_value.read.return_value = buf.getvalue()
                self.assertEqual(synthesize(text, asr_check=False), buf.getvalue())
                self.assertEqual(request.call_count, 1)
                payload = json.loads(request.call_args.args[0].data)
                self.assertEqual(payload["text"], text)
                self.assertEqual(payload["text_split_method"], method)
                self.assertEqual(payload["prompt_lang"], "zh")

    def test_asr_switch_checks_plain_units_or_bypasses_all_checks(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00\x00" * 160)
        body = buf.getvalue()
        recovery = {"requests": [], "attempts": [], "recovered": False}
        with patch("server.app.guarded_synthesize", return_value=(body, recovery)) as guarded:
            self.assertEqual(synthesize("普通句子。", asr_check=True), body)
            guarded.assert_called_once()
        with patch("server.app.synthesize_chunk", return_value=body), patch("server.app.guarded_synthesize") as guarded:
            self.assertEqual(synthesize("那是什么...嗯？", asr_check=False), body)
            guarded.assert_not_called()

    def test_android_header_selects_all_or_no_asr_and_separates_cache(self):
        key = store.add([{"text": "测试"}])
        with patch("server.app.synthesize", return_value=b"RIFFxxxxWAVE") as synth:
            all_headers = {**self.auth, "X-Reader-ASR-Check": "all"}
            off_headers = {**self.auth, "X-Reader-ASR-Check": "off"}
            self.assertEqual(self.client.get(f"/pages/{key}/audio/0", headers=all_headers).status_code, 200)
            self.assertTrue(synth.call_args.kwargs["asr_check"])
            self.assertEqual(self.client.get(f"/pages/{key}/audio/0", headers=off_headers).status_code, 200)
            self.assertFalse(synth.call_args.kwargs["asr_check"])
            self.assertEqual(synth.call_count, 2)
        store.cancel(key)
        self.assertEqual(
            self.client.get("/pages/missing/audio/0", headers={**self.auth, "X-Reader-ASR-Check": "sometimes"}).status_code,
            422,
        )

    def test_android_speed_header_reaches_tts_and_separates_cache(self):
        key = store.add([{"text": "通用速度测试"}])
        try:
            with patch("server.app.synthesize", return_value=b"RIFFxxxxWAVE") as synth:
                slow = {**self.auth, "X-Reader-Speech-Speed": "0.50"}
                fast = {**self.auth, "X-Reader-Speech-Speed": "1.50"}
                self.assertEqual(self.client.get(f"/pages/{key}/audio/0", headers=slow).status_code, 200)
                self.assertEqual(synth.call_args.kwargs["speed_factor"], 0.5)
                self.assertEqual(self.client.get(f"/pages/{key}/audio/0", headers=slow).status_code, 200)
                self.assertEqual(synth.call_count, 1)
                self.assertEqual(self.client.get(f"/pages/{key}/audio/0", headers=fast).status_code, 200)
                self.assertEqual(synth.call_args.kwargs["speed_factor"], 1.5)
                self.assertEqual(synth.call_count, 2)
                for invalid in ("0.49", "0.499", "1.51", "nan", "invalid"):
                    response = self.client.get(
                        f"/pages/{key}/audio/0",
                        headers={**self.auth, "X-Reader-Speech-Speed": invalid},
                    )
                    self.assertEqual(response.status_code, 422)
                self.assertEqual(synth.call_count, 2)
        finally:
            store.cancel(key)

    def test_configured_voice_reaches_health_and_page(self):
        with patch.dict(settings, {"voice": "luoxi", "speech_mode": "native"}):
            health = self.client.get("/health").json()
            self.assertEqual(health["voice"], "luoxi")
            self.assertEqual(health["speech_policy"]["guard"], settings["speech_guard"])
            page = self.client.post("/pages/text", headers=self.auth, json={"text": "测试中文。"}).json()
            self.assertEqual(page["sentences"][0]["speaker_id"], "luoxi")
            store.cancel(page["page_id"])

    def test_startup_warmup_requires_auth_and_is_idempotent(self):
        original = dict(warmup_state)
        try:
            warmup_state.update(status="cold", seconds=0.0, steps={})
            with patch("server.app.warm_ocr_component") as warm_ocr, patch(
                "server.app.warm_speech_component",
                return_value={"tts_seconds": 1.0, "asr_seconds": 2.0},
            ) as warm_speech:
                self.assertEqual(self.client.post("/warmup").status_code, 401)
                first = self.client.post("/warmup", headers=self.auth)
                second = self.client.post("/warmup", headers=self.auth)
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json()["status"], "ready")
                self.assertFalse(first.json()["cached"])
                self.assertEqual(second.json()["status"], "ready")
                self.assertTrue(second.json()["cached"])
                warm_ocr.assert_called_once()
                warm_speech.assert_called_once()
                self.assertEqual(self.client.get("/health").json()["warmup_status"], "ready")
        finally:
            warmup_state.clear()
            warmup_state.update(original)

    def test_model_catalog_requires_auth_and_selects_under_api(self):
        catalog={"models":[],"tree":[],"counts":{"total":0},"active_id":None}
        self.assertEqual(self.client.get("/models").status_code,401)
        with patch("server.app.model_catalog.scan",return_value=catalog):
            self.assertEqual(self.client.get("/models",headers=self.auth).json(),catalog)
        selected={"id":"abc","name":"角色","version":"v4","state":"available"}
        with patch("server.app.model_catalog.select",return_value=selected) as choose, patch.object(store,"clear") as clear:
            response=self.client.post("/models/select",headers=self.auth,json={"model_id":"abc"})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()["selected"],selected)
            self.assertEqual(choose.call_args.args[:2],("abc",True));self.assertTrue(callable(choose.call_args.args[2]));clear.assert_called_once()
        self.assertEqual(self.client.post("/models/select",headers=self.auth,json={}).status_code,422)

    def test_model_selection_errors_are_explicit(self):
        with patch("server.app.model_catalog.select",side_effect=ValueError("模型文件不完整")):
            response=self.client.post("/models/select",headers=self.auth,json={"model_id":"bad"})
            self.assertEqual(response.status_code,422);self.assertIn("模型文件不完整",response.text)
        with patch("server.app.model_catalog.select",side_effect=RuntimeError("模型加载失败")):
            response=self.client.post("/models/select",headers=self.auth,json={"model_id":"bad"})
            self.assertEqual(response.status_code,502);self.assertIn("模型加载失败",response.text)
    def test_pair_checks_token(self):
        self.assertEqual(self.client.get("/pair").status_code, 401)
        pair = self.client.get(
            "/pair", headers={**self.auth, "X-Reader-Version": "4"}
        )
        self.assertEqual(pair.status_code, 200)
        self.assertEqual(pair.json()["version"], READER_PROTOCOL_VERSION)
        self.assertEqual(self.client.get("/health").json()["version"], 4)

    def test_computer_warns_for_old_or_different_android_without_blocking(self):
        import server.app as reader_app
        original = reader_app.last_android_version_state
        try:
            reader_app.last_android_version_state = None
            with patch("server.app.event") as debug:
                self.assertEqual(observe_android_version(None), "missing")
                debug.assert_called_once_with(
                    "android_version_missing", computer_version=4
                )
                debug.reset_mock()
                self.assertEqual(observe_android_version(None), "missing")
                debug.assert_not_called()
                self.assertEqual(observe_android_version("3"), "mismatch")
                debug.assert_called_once_with(
                    "android_version_mismatch",
                    android_version=3,
                    computer_version=4,
                )
                debug.reset_mock()
                self.assertEqual(observe_android_version("4"), "match")
                debug.assert_not_called()
                self.assertEqual(observe_android_version("invalid"), "missing")
                debug.assert_called_once_with(
                    "android_version_missing", computer_version=4
                )
            response = self.client.get(
                "/pair", headers={"X-Reader-Token": settings["token"]}
            )
            self.assertEqual(response.status_code, 200)
        finally:
            reader_app.last_android_version_state = original

    def test_corrected_text_order_and_cancellation(self):
        response = self.client.post(
            "/pages/text",
            headers=self.auth,
            json={"text": "旁白。\n第2句：2026年！\n没加引号也朗读"},
        )
        self.assertEqual(response.status_code, 200)
        page = response.json()
        self.assertEqual(
            [s["text"] for s in page["sentences"][0]["segments"]],
            ["旁白。", "第2句：2026年！", "没加引号也朗读"],
        )
        self.assertEqual(len(page["sentences"]), 1)
        self.assertEqual(
            page["sentences"][0]["text"], "旁白！第2句：2026年！没加引号也朗读。"
        )
        self.client.delete("/pages/" + page["page_id"], headers=self.auth)
        with patch("server.app.synthesize") as synth:
            self.assertEqual(
                self.client.get(
                    "/pages/" + page["page_id"] + "/audio/0", headers=self.auth
                ).status_code,
                404,
            )
            synth.assert_not_called()

    def test_invalid_and_oversized_text(self):
        for value in ("", "English only!", None, 3, [], "字" * 20001):
            self.assertEqual(
                self.client.post(
                    "/pages/text", headers=self.auth, json={"text": value}
                ).status_code,
                422,
            )

    def test_cancel_during_synthesis_does_not_return_stale_audio(self):
        key = store.add([{"text": "测试"}])

        def synth(text, **kwargs):
            store.cancel(key)
            return b"RIFFxxxxWAVE"

        with patch("server.app.synthesize", side_effect=synth):
            self.assertEqual(
                self.client.get(
                    "/pages/" + key + "/audio/0", headers=self.auth
                ).status_code,
                410,
            )

    def test_shared_audio_task_deduplicates_same_mode(self):
        key = store.add([{"text": "测试"}])
        async def run():
            return await asyncio.gather(
                ensure_audio(key, 0, False, 1.0),
                ensure_audio(key, 0, False, 1.0),
            )
        with patch("server.app.synthesize", return_value=b"audio") as build:
            self.assertEqual(asyncio.run(run()), [b"audio", b"audio"])
            self.assertEqual(build.call_count, 1)
        self.assertFalse(audio_tasks)
        store.cancel(key)

    def test_audio_modes_are_isolated_and_failure_can_retry(self):
        key = store.add([{"text": "测试"}])
        with patch("server.app.synthesize", side_effect=[RuntimeError("临时失败"), b"normal", b"fast"]) as build:
            with self.assertRaises(Exception):
                asyncio.run(ensure_audio(key, 0, True, 1.0))
            self.assertEqual(asyncio.run(ensure_audio(key, 0, True, 1.0)), b"normal")
            self.assertEqual(asyncio.run(ensure_audio(key, 0, True, 1.2)), b"fast")
            self.assertEqual(build.call_count, 3)
        self.assertFalse(audio_tasks)
        store.cancel(key)

    def test_full_page_prefetch_builds_every_unit_in_order(self):
        key = store.add([{"text": f"第{i}句"} for i in range(4)])
        page = store.get(key)
        page["full_page_prefetch"] = True
        select_prefetch_mode(page, True, 1.0)
        order = []
        def build(text, **kwargs):
            order.append(text)
            return text.encode("utf-8")
        try:
            with patch("server.app.synthesize", side_effect=build):
                asyncio.run(prefetch_page_audio(key))
            self.assertEqual(order, ["第0句", "第1句", "第2句", "第3句"])
            self.assertEqual(sorted(page["audio"]), [0, 1, 2, 3])
            self.assertTrue(all(mode == (True, 1.0) for mode in page["audio_modes"].values()))
        finally:
            store.cancel(key)

    def test_full_page_prefetch_is_bounded_and_mode_changes_invalidate_queue(self):
        key = store.add([{"text": f"第{i}句"} for i in range(5)])
        page = store.get(key)
        page["full_page_prefetch"] = True
        original_generation = select_prefetch_mode(page, True, 1.0)
        self.assertEqual(select_prefetch_mode(page, True, 1.0), original_generation)
        self.assertGreater(select_prefetch_mode(page, True, 1.2), original_generation)
        try:
            with patch.dict(settings, {"full_page_prefetch_max_units": 2}), patch(
                "server.app.synthesize", return_value=b"audio"
            ) as build:
                asyncio.run(prefetch_page_audio(key))
            self.assertEqual(build.call_count, 2)
            self.assertEqual(sorted(page["audio"]), [0, 1])
            self.assertTrue(all(mode == (True, 1.2) for mode in page["audio_modes"].values()))
        finally:
            store.cancel(key)

    def test_pipeline_headers_are_optional_and_validated(self):
        class OCR:
            def read(self, body, parallel=False):
                self.parallel = parallel
                return []
        fake = OCR()
        with patch("server.app.ocr", fake):
            response = self.client.post("/pages", headers=self.auth, content=b"fixture")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(fake.parallel)
            headers = {**self.auth, "X-Reader-Parallel-Vision": "on"}
            self.assertEqual(self.client.post("/pages", headers=headers, content=b"fixture").status_code, 200)
            self.assertTrue(fake.parallel)
            headers["X-Reader-Parallel-Vision"] = "invalid"
            self.assertEqual(self.client.post("/pages", headers=headers, content=b"fixture").status_code, 422)
            headers = {**self.auth, "X-Reader-Full-Page-Prefetch": "invalid"}
            self.assertEqual(self.client.post("/pages", headers=headers, content=b"fixture").status_code, 422)


if __name__ == "__main__":
    unittest.main()
