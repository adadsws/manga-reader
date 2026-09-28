import io, json, re, unittest, wave, threading, time
import numpy as np
from pathlib import Path
from unittest.mock import patch
from server.core import normalize_speech_text, speech_chunks, filter_speech_candidates, group_lines, compact_reading_order, map_text_to_panels
from server.ocr import MangaOCR, Region, sort
from server.layout import MangaLayout
from server.app import synthesize, settings
F = json.loads((Path(__file__).parent / "fixtures/reading_feedback.json").read_text(encoding="utf-8"))
class ReadingFeedbackTests(unittest.TestCase):
    def test_ocr_requests_cuda_for_all_onnx_submodels(self):
        class Session:
            def get_providers(self): return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        component = type("Component", (), {"session": type("Wrapper", (), {"session": Session()})()})()
        class Engine:
            text_det = text_cls = text_rec = component
        class Layout:
            device = "cuda"
        with patch("server.ocr.RapidOCR", return_value=Engine()) as rapid, patch("server.ocr.MangaLayout", return_value=Layout()) as layout:
            instance = MangaOCR("cuda")
        self.assertTrue(rapid.call_args_list[0].kwargs["params"]["EngineConfig.onnxruntime.use_cuda"])
        self.assertFalse(rapid.call_args_list[1].kwargs["params"]["EngineConfig.onnxruntime.use_cuda"])
        self.assertEqual(instance.ocr_backend, "cuda")
        self.assertEqual(rapid.call_count, 2)
        layout.assert_called_once_with("cuda")

    def test_ocr_rejects_cpu_provider_when_cuda_is_required(self):
        class Session:
            def get_providers(self): return ["CPUExecutionProvider"]
        component = type("Component", (), {"session": type("Wrapper", (), {"session": Session()})()})()
        engine = type("Engine", (), {"text_det": component, "text_cls": component, "text_rec": component})()
        with patch("server.ocr.RapidOCR", return_value=engine), patch("server.ocr.MangaLayout"):
            with self.assertRaisesRegex(RuntimeError, "CUDAExecutionProvider"):
                MangaOCR("cuda")

    def test_ocr_initialization_failure_does_not_retry_on_cpu(self):
        with patch("server.ocr.RapidOCR", side_effect=RuntimeError("CUDA 初始化失败")) as rapid:
            with self.assertRaisesRegex(RuntimeError, "CUDA 初始化失败"):
                MangaOCR("cuda")
        self.assertEqual(rapid.call_count, 1)

    def test_layout_rejects_missing_cuda_before_loading_model(self):
        with patch("server.layout.torch.cuda.is_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "PyTorch CUDA"):
                MangaLayout("cuda")

    def test_short_row_refinement_uses_dedicated_engine(self):
        class Result:
            boxes = None
        instance = MangaOCR.__new__(MangaOCR)
        instance.engine = lambda *args, **kwargs: self.fail("主 OCR 不应处理小块复识")
        instance.refine_engine = lambda *args, **kwargs: Result()
        row = {"text": "小字", "box": [2, 2, 12, 22], "score": .99}
        frame = np.zeros((30, 30, 3), dtype=np.uint8)
        actual = instance.refine_short_rows(frame, [row], [{"box": [0, 0, 30, 30]}])
        self.assertEqual(actual, [row])

    def test_parallel_vision_overlaps_independent_models(self):
        ocr_started, layout_started = threading.Event(), threading.Event()
        case = self
        class Empty:
            boxes = None
        instance = MangaOCR.__new__(MangaOCR)
        def engine(frame):
            ocr_started.set()
            self.assertTrue(layout_started.wait(1))
            time.sleep(.03)
            return Empty()
        class Layout:
            def panels(self, frame):
                layout_started.set()
                case.assertTrue(ocr_started.wait(1))
                time.sleep(.03)
                return []
        instance.engine, instance.layout = engine, Layout()
        with patch("server.ocr.cv2.imdecode", return_value=np.zeros((20, 20, 3), dtype=np.uint8)):
            self.assertEqual(instance.read(b"image", parallel=True), [])
        self.assertTrue(instance.last_timings["parallel"])

    def test_parallel_and_serial_vision_keep_output_order(self):
        class Result:
            boxes = [np.array([[10, 10], [20, 10], [20, 40], [10, 40]], dtype=float)]
            txts = ["测试文字"]
            scores = [.9]
        class Layout:
            text_regions = []
            def panels(self, frame): return []
        instance = MangaOCR.__new__(MangaOCR)
        instance.engine, instance.layout = lambda frame: Result(), Layout()
        instance.cc = type("CC", (), {"convert": lambda self, value: value})()
        frame = np.zeros((60, 60, 3), dtype=np.uint8)
        with patch("server.ocr.cv2.imdecode", return_value=frame):
            serial = instance.read(b"image", parallel=False)
            parallel = instance.read(b"image", parallel=True)
        self.assertEqual(serial, parallel)

    def test_ocr_warmup_uses_generated_memory_image(self):
        instance = MangaOCR.__new__(MangaOCR)
        with patch.object(instance, "read", return_value=[]) as read:
            instance.warmup()
        body = read.call_args.args[0]
        self.assertGreater(len(body), 1000)
        self.assertEqual(body[:2], b"\xff\xd8")

    def test_normalization(self):
        self.assertEqual(normalize_speech_text("憨成這樣 SUMUMERFRUITS 第2句，2026年。"), "憨成这样  第2句，2026年。")
        self.assertEqual(normalize_speech_text("SUMUMERFRUITS！"), "")
        self.assertEqual(normalize_speech_text("E7。ーナ。は。"), "")
    def test_ellipsis_forms_and_idempotence(self):
        for mark in ('…','……','⋯','⋯⋯','︙','⋮','⋰','⋱','‥','..','......','．．．','。。。','···','. . .','…⋯...'):
            with self.subTest(mark=mark):
                self.assertEqual(normalize_speech_text('等等'+mark+'是什么？'),'等等...是什么？')
        text='数值3.14。等等...嗯？'
        self.assertEqual(normalize_speech_text(text),text)
        self.assertEqual(normalize_speech_text(normalize_speech_text(text)),text)

    def test_ellipsis_does_not_create_panel_sentence_boundary(self):
        from server.core import prepare_panel_sentences
        rows=[dict(text='等等……',panel_id=0,panel_box=[0,0,10,10]),dict(text='原来如此。',panel_id=0,panel_box=[0,0,10,10])]
        self.assertEqual([r['text'] for r in prepare_panel_sentences(rows,'luoxi')],['等等...原来如此。'])

    def test_chunk_coverage(self):
        for text in ("第一句。第二句！第三句？"*15, "长段测试"*20):
            chunks = speech_chunks(text)
            self.assertEqual("".join(chunks), normalize_speech_text(text))
            self.assertTrue(all(0<len(c)<=24 for c in chunks))
    def test_user_orders(self):
        for name, desired in (("0014.jpg", ["嗯", "人家", "算了", "那个", "哪个"]), ("0024.jpg", ["这是称赞", "嗯", "是吗", "不是称赞", "妳叫", "美铃"])):
            f=F[name]; groups=group_lines(filter_speech_candidates(f["rows"], f["text_regions"])[0])
            mapping=map_text_to_panels([g["box"] for g in groups],f["panels"])
            selected=[g for g,p in zip(groups,mapping) if p==max(mapping)]
            order=compact_reading_order(selected); self.assertIsNotNone(order)
            text=normalize_speech_text("".join(selected[i]["original"] for i in order))
            offsets=[text.index(word) for word in desired]
            self.assertEqual(offsets,sorted(offsets),name)
    def test_old_panel_orders(self):
        fixtures=json.loads((Path(__file__).parent/"fixtures/nested_panels.json").read_text(encoding="utf-8"))
        for name,f in fixtures.items():
            panels={}
            for i,p in enumerate(map_text_to_panels([g["box"] for g in f["rows"]],f["panels"])): panels.setdefault(p,[]).append(dict(f["rows"][i],index=i))
            actual=[]
            for p,groups in sorted(panels.items()):
                order=compact_reading_order(groups)
                ordered=[groups[i] for i in order] if order is not None else [r.data for r in sort._simple_sort([Region(g) for g in groups],True)]
                actual.extend(g["index"] for g in ordered)
            self.assertEqual(actual,f["expected_order"],name)
    def test_noise_and_controls(self):
        f=F["0023.jpg"];kept,excluded=filter_speech_candidates(f["rows"],f["text_regions"])
        self.assertEqual(kept,f["rows"][7:11]);self.assertEqual(len(kept)+len(excluded),len(f["rows"]))
        f=F["0027.jpg"];kept,_=filter_speech_candidates(f["rows"],f["text_regions"])
        self.assertNotIn(f["rows"][7], kept)
        self.assertFalse(any(re.fullmatch(r"[0-9A-Za-z。]+",r["text"]) for r in kept))
        rows=[{"text":"2026年","box":[0,0,20,80],"score":.99},{"text":"中文旁白","box":[200,0,240,80],"score":.9},{"text":"嗯","box":[0,0,20,20],"score":.9}]
        self.assertEqual(filter_speech_candidates(rows,[{"box":[0,0,20,80],"dialogue_score":.9}])[0],rows)
        self.assertEqual(filter_speech_candidates(rows,[])[0],rows)
    def test_audio_pcm_coverage(self):
        calls=[]
        def chunk(text, **kwargs):
            calls.append(text);out=io.BytesIO()
            with wave.open(out,"wb") as w:
                w.setparams((1,2,32000,0,"NONE","not compressed"));w.writeframes(bytes([len(calls),0])*320)
            return out.getvalue()
        text="验证每一段音频都被保留下来。"*6
        with patch.dict(settings,{"speech_mode":"bounded"}), patch("server.app.synthesize_chunk",side_effect=chunk):body=synthesize(text, asr_check=False)
        with wave.open(io.BytesIO(body),"rb") as w:
            self.assertEqual(w.getnframes(),len(calls)*320)
            self.assertEqual(w.readframes(w.getnframes()),b"".join(bytes([i,0])*320 for i in range(1,len(calls)+1)))
        self.assertEqual("".join(calls),normalize_speech_text(text))

    def test_cancel_stops_before_next_chunk(self):
        calls=[]
        def chunk(text, **kwargs):
            calls.append(text)
            return b"discarded"
        with patch("server.app.synthesize_chunk", side_effect=chunk):
            with self.assertRaisesRegex(ValueError, "页面已取消"):
                synthesize("足够长的第一段测试。"*8, cancelled=lambda: bool(calls), asr_check=False)
        self.assertEqual(len(calls),1)

    def test_rejects_truncated_wav(self):
        with patch("server.app.urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value=b"RIFFxxxxWAVE"
            with self.assertRaises((wave.Error, EOFError)):
                synthesize("测试音频。", asr_check=False)

    def test_high_confidence_short_chinese_with_nearby_sentence(self):
        fixtures=json.loads((Path(__file__).parent/'fixtures/short_text_omissions.json').read_text(encoding='utf-8'))
        for f in fixtures.values():
            target=f['targets'][0]
            regions=[dict(box=target['box'],dialogue_score=target['dialogue_score'])]
            kept,_=filter_speech_candidates(f['rows'],regions)
            self.assertTrue(any(r['text']==target['text'] and r['box']==target['box'] for r in kept))
