# 局域网入口：截图识别、整页音频与取消。模型计算与 Android 解耦。
import asyncio
import json
import io
import wave
import hashlib
import math
import threading
import time
import urllib.request
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, Response
from .core import PageStore, split_sentences, merge_page, normalize_speech_text, speech_chunks, prepare_panel_sentences
from .ocr import MangaOCR
from .debug import event
from .tts_guard import guarded_synthesize, recognize, asr_status
from .model_catalog import ModelCatalog
from .history import HistoryStore, compound_key, digest_bytes, digest_json

ROOT = Path(__file__).resolve().parents[1]
READER_PROTOCOL_VERSION = 4
settings = json.loads((ROOT / "config/reader.json").read_text(encoding="utf-8"))
app = FastAPI(title="漫画朗读电脑服务")
store = PageStore()
ocr = None
ocr_lock = asyncio.Lock()
tts_lock = asyncio.Lock()
audio_tasks = {}
page_prefetch_tasks = {}
model_catalog = ModelCatalog(ROOT / "models/gpt-sovits", settings)
history = HistoryStore()
vision_lock_signatures = [
    [name, digest_bytes((ROOT / "config" / name).read_bytes())]
    for name in ("ocr-models.lock.json", "layout-model.lock.json", "upstream-files.lock.json")
]
vision_code_signatures = [
    [name, digest_bytes((ROOT / "server" / name).read_bytes())]
    for name in ("ocr.py", "layout.py", "core.py")
]
tts_code_signatures = [
    [name, digest_bytes((ROOT / "server" / name).read_bytes())]
    for name in ("app.py", "core.py", "tts_guard.py")
]
_tts_history_context = None
warmup_lock = asyncio.Lock()
warmup_state = {"status": "cold", "seconds": 0.0, "steps": {}}
pair_version_lock = threading.Lock()
last_android_version_state = None


@app.middleware("http")
async def debug_requests(request, call_next):
    started = time.monotonic()
    # 心跳查询保持安静；只记录 HTTP 状态与耗时，不记录用户可控的 URL。
    monitored = request.url.path != '/health'
    if monitored: event('request_start')
    try:
        response = await call_next(request)
    except Exception:
        event('request_error', seconds=round(time.monotonic()-started, 3))
        raise
    if monitored: event('request_end', status=response.status_code, seconds=round(time.monotonic()-started, 3))
    return response


def authorize(request):
    if request.headers.get("X-Reader-Token") != settings["token"]:
        raise HTTPException(401, "配对口令错误")


def enabled_header(request, name, default=True):
    value = request.headers.get(name)
    if value is None:
        return default
    if value not in ("on", "off"):
        raise HTTPException(422, f"{name} 只能是 on 或 off")
    return value == "on"


def history_options(request):
    return (
        enabled_header(request, "X-Reader-History-Reuse", True),
        enabled_header(request, "X-Reader-History-Hidden", False),
    )


def _file_signature(path):
    try:
        value = Path(path)
        stat = value.stat()
        return [str(value.resolve()), stat.st_size, stat.st_mtime_ns]
    except (OSError, TypeError):
        return [str(path), None, None]


def vision_fingerprint(parallel):
    return digest_json({
        "version": 1,
        "device": settings.get("ocr_device", "cuda"),
        "parallel": bool(parallel),
        "locks": vision_lock_signatures,
        "code": vision_code_signatures,
    })


def ocr_structure_key(rows):
    return compound_key("ocr-structure-v2", rows, vision_code_signatures)


def reading_text_key(sentences):
    units = [
        {
            "text": normalize_speech_text(row.get("text", "")),
            "mode": row.get("mode"),
        }
        for row in sentences
    ]
    return compound_key("reading-text-v1", units)


def tts_history_context():
    global _tts_history_context
    if _tts_history_context is None:
        _tts_history_context = {
            "model_id": model_catalog.active_id or model_catalog.requested_id,
            "voice": settings.get("voice"),
            "model_version": settings.get("model_version"),
            "tts_url": settings.get("tts_url"),
            "model_artifacts": model_catalog.active_artifact_signatures(),
            "reference_audio": _file_signature(settings.get("reference_audio", "")),
            "reference_text": settings.get("reference_text"),
            "prompt_lang": settings.get("prompt_lang", "ja"),
            "text_split_method": settings.get("text_split_method", "cut2"),
            "speech_mode": settings.get("speech_mode", "bounded"),
            "batch_size": settings.get("tts_batch_size", 1),
            "parallel_infer": settings.get("tts_parallel_infer", False),
            "seed": settings.get("seed", 42),
            "repetition_penalty": settings.get("repetition_penalty", 1.35),
            "top_k": settings.get("top_k", 15),
            "code": tts_code_signatures,
        }
    return _tts_history_context


def audio_history_key(text, asr_check, speed_factor):
    return compound_key("audio-v1", normalize_speech_text(text), tts_history_context(), {
        "asr_check": bool(asr_check),
        "speed_factor": speed_factor,
    })


@app.get("/health")
def health():
    ocr_status = {
        "requested_device": settings.get("ocr_device", "cuda"),
        "backend": getattr(ocr, "ocr_backend", "not_loaded"),
        "providers": getattr(ocr, "ocr_providers", {}),
        "refine_providers": getattr(ocr, "refine_providers", {}),
        "layout_backend": getattr(getattr(ocr, "layout", None), "device", "not_loaded"),
        "failure_policy": "strict_no_device_fallback",
    }
    return {"status": "ok", "voice": settings.get("voice", "hina"), "voice_name": settings.get("voice_name", settings.get("voice", "hina")), "model_version": settings.get("model_version", "v4"), "active_model_id": model_catalog.active_id, "processing": "computer", "version": READER_PROTOCOL_VERSION, "warmup_status": warmup_state["status"], "history": {"lookup": "exact_memory", "persistence": "sqlite_wal_async", "writer_errors": len(history.errors)}, "ocr": ocr_status, "asr": asr_status(), "speech_policy": {"normalization": "t2s_skip_english_kana", "max_chunk_characters": None if settings.get("speech_mode") == "native" else 24, "text_split_method": settings.get("text_split_method", "cut2"), "mode": settings.get("speech_mode", "bounded"), "unit": "panel_sentence", "guard": settings.get("speech_guard", "off"), "repetition_penalty": settings.get("repetition_penalty", 1.35), "parallel_infer": settings.get("tts_parallel_infer", False), "batch_size": settings.get("tts_batch_size", 1), "seed": 42}}


@app.get("/pair")
def pair(request: Request):
    authorize(request)
    observe_android_version(request.headers.get("X-Reader-Version"))
    return health()


def observe_android_version(raw_version):
    global last_android_version_state
    android_version = None
    if raw_version is not None:
        try:
            android_version = int(raw_version)
        except (TypeError, ValueError):
            pass
    if android_version is None:
        state = ("missing", None)
    elif android_version == READER_PROTOCOL_VERSION:
        state = ("match", android_version)
    else:
        state = ("mismatch", android_version)
    with pair_version_lock:
        changed = state != last_android_version_state
        last_android_version_state = state
    if changed and state[0] == "missing":
        event("android_version_missing", computer_version=READER_PROTOCOL_VERSION)
    elif changed and state[0] == "mismatch":
        event(
            "android_version_mismatch",
            android_version=android_version,
            computer_version=READER_PROTOCOL_VERSION,
        )
    return state[0]


def warm_ocr_component():
    global ocr
    if ocr is None:
        ocr = MangaOCR(settings.get("ocr_device", "cuda"))
    ocr.warmup()


def warm_speech_component():
    timings = {}
    started = time.monotonic()
    event("warmup_tts_start")
    body = synthesize_chunk("启动预热。")
    timings["tts_seconds"] = round(time.monotonic() - started, 3)
    event("warmup_tts_ready", seconds=timings["tts_seconds"])
    started = time.monotonic()
    event("warmup_asr_start")
    recognize(body)
    timings["asr_seconds"] = round(time.monotonic() - started, 3)
    event("warmup_asr_ready", seconds=timings["asr_seconds"])
    return timings


@app.post("/warmup")
async def warmup(request: Request):
    authorize(request)
    if warmup_state["status"] == "ready":
        return {**warmup_state, "cached": True}
    async with warmup_lock:
        if warmup_state["status"] == "ready":
            return {**warmup_state, "cached": True}
        started = time.monotonic()
        warmup_state.update(status="warming", seconds=0.0, steps={})
        event("warmup_start")
        try:
            step = time.monotonic()
            event("warmup_ocr_start")
            async with ocr_lock:
                await asyncio.to_thread(warm_ocr_component)
            warmup_state["steps"]["ocr_seconds"] = round(time.monotonic() - step, 3)
            event("warmup_ocr_ready", seconds=warmup_state["steps"]["ocr_seconds"])
            async with tts_lock:
                if model_catalog.loaded_id != model_catalog.active_id:
                    await asyncio.to_thread(model_catalog.ensure_loaded)
                warmup_state["steps"].update(await asyncio.to_thread(warm_speech_component))
            warmup_state.update(status="ready", seconds=round(time.monotonic() - started, 3))
            event("warmup_ready", seconds=warmup_state["seconds"])
            return {**warmup_state, "cached": False}
        except Exception as exc:
            warmup_state.update(status="failed", seconds=round(time.monotonic() - started, 3))
            event("warmup_error", reason=str(exc))
            raise HTTPException(502, "启动预热失败：" + str(exc))


@app.get("/models")
def models(request: Request):
    authorize(request)
    return model_catalog.scan()


@app.get("/models/{model_id}/avatar")
def model_avatar(model_id: str, request: Request):
    authorize(request)
    path = model_catalog.avatar(model_id)
    if not path or not path.is_file():
        raise HTTPException(404, "模型没有可用头像")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.post("/models/select")
async def select_model(request: Request):
    global _tts_history_context
    authorize(request)
    try:
        body = await request.json()
        model_id = body["model_id"]
        if not isinstance(model_id, str) or len(model_id) > 80:
            raise ValueError("model_id 无效")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "需要有效的 model_id")
    event("model_load_start")
    async with tts_lock:
        try:
            selected = await asyncio.to_thread(model_catalog.select, model_id, True, lambda: synthesize_chunk("模型测试。"))
            _tts_history_context = None
            store.clear()
        except ValueError as exc:
            event("model_load_error", reason=str(exc))
            raise HTTPException(422, str(exc))
        except Exception as exc:
            event("model_load_error", reason=str(exc))
            raise HTTPException(502, str(exc))
    event("model_load_ready")
    return {"selected": selected, "health": health()}

@app.post("/pages")
async def page(request: Request):
    global ocr
    authorize(request)
    reuse_history, hide_history = history_options(request)
    parallel = enabled_header(request, "X-Reader-Parallel-Vision", False)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 12000000:
            raise HTTPException(413, "截图超过 12 MB")
    raw_body = bytes(body)
    input_sha = digest_bytes(raw_body)
    image_key = compound_key("image-v1", input_sha, vision_fingerprint(parallel))
    match_stage = "disabled" if not reuse_history else "none"
    source_version = None
    result_version = None
    skipped_stages = []
    image_hit = history.lookup_page("image", image_key) if reuse_history else None
    raw_rows = None
    if image_hit:
        sentences = image_hit["sentences"]
        source_version = image_hit["version_id"]
        result_version = source_version
        match_stage = "image"
        skipped_stages = ["ocr", "layout", "sort", "text_prepare"]
        event("history_hit", stage="image")
    else:
        event("ocr_queued", bytes=len(body))
        async with ocr_lock:
            try:
                started = time.monotonic()
                if ocr is None:
                    event("ocr_model_loading")
                    ocr = await asyncio.to_thread(MangaOCR, settings.get("ocr_device", "cuda"))
                    event("ocr_model_ready")
                event("ocr_start", bytes=len(body))
                raw_rows = await asyncio.to_thread(ocr.read, raw_body, True) if parallel else await asyncio.to_thread(ocr.read, raw_body)
                timing = getattr(ocr, "last_timings", {})
                event("ocr_end", rows=len(raw_rows), seconds=round(time.monotonic()-started, 3), **timing)
            except Exception as e:
                event("ocr_error")
                history.record_run(input_kind="image", input_sha=input_sha,
                                   match_stage=match_stage, source_version=None,
                                   result_version=None, reuse=reuse_history,
                                   hidden=hide_history, status="failed")
                raise HTTPException(422, str(e))
        ocr_key = ocr_structure_key(raw_rows)
        ocr_hit = history.lookup_page("ocr", ocr_key) if reuse_history else None
        if ocr_hit:
            sentences = ocr_hit["sentences"]
            source_version = ocr_hit["version_id"]
            match_stage = "ocr"
            skipped_stages = ["text_prepare"]
            event("history_hit", stage="ocr")
        else:
            sentences = prepare_panel_sentences(raw_rows, settings.get("voice", "hina"))
        text_key = reading_text_key(sentences)
        if reuse_history and match_stage == "none":
            text_hit = history.lookup_page("text", text_key)
            if text_hit:
                source_version = text_hit["version_id"]
                match_stage = "text"
                event("history_hit", stage="text")
        result_version = history.register_page(
            input_kind="image", input_sha=input_sha, image_key=image_key,
            ocr_key=ocr_key, text_key=text_key, sentences=sentences,
            source_version=source_version, match_stage=match_stage,
            hidden=hide_history, input_body=raw_body,
        )
    if image_hit:
        text_key = reading_text_key(sentences)
    history.record_run(
        input_kind="image", input_sha=input_sha, match_stage=match_stage,
        source_version=source_version, result_version=result_version,
        reuse=reuse_history, hidden=hide_history,
    )
    key = store.add(
        sentences,
        history_reuse=reuse_history,
        history_hidden=hide_history,
        history_version_id=result_version,
        history_match_stage=match_stage,
        history_text_key=text_key,
    )
    if settings.get("save_debug_pages", False):
        evidence = ROOT / "~outputs-intermediate/pages"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / (key + ".jpg")).write_bytes(body)
        (evidence / (key + ".json")).write_text(
            json.dumps(sentences, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    full_page_prefetch = enabled_header(request, "X-Reader-Full-Page-Prefetch", False)
    if sentences and (enabled_header(request, "X-Reader-Eager-First-Audio", False) or full_page_prefetch):
        asr_check, speed_factor = audio_options(request)
        if full_page_prefetch:
            p = store.get(key)
            p["full_page_prefetch"] = True
            select_prefetch_mode(p, asr_check, speed_factor)
            schedule_page_prefetch(key)
        else:
            task = asyncio.create_task(ensure_audio(key, 0, asr_check, speed_factor, "eager"))
            task.add_done_callback(consume_task_exception)
    return {
        "page_id": key, "sentences": sentences,
        "history_version_id": result_version,
        "match_stage": match_stage,
        "skipped_stages": skipped_stages,
    }


@app.post("/pages/text")
async def corrected_page(request: Request):
    authorize(request)
    reuse_history, hide_history = history_options(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 80000:
            raise HTTPException(413, "校对文本过长")
    try:
        text = json.loads(body)["text"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "需要 text 字段")
    if not isinstance(text, str) or len(text) > 20000:
        raise HTTPException(422, "文本必须是最多 20000 字的字符串")
    input_body = text.encode("utf-8")
    input_sha = digest_bytes(input_body)
    sentences = [
        {"original": t, "text": t, "box": [], "confidence": 1.0, "speaker_id": "hina"}
        for t in split_sentences(text)
    ]
    if not sentences:
        raise HTTPException(422, "至少保留一句文字")
    for row in sentences:
        row["speaker_id"] = settings.get("voice", "hina")
    sentences = merge_page(sentences)
    if not sentences:
        raise HTTPException(422, "没有可朗读的中文或数字")
    text_key = reading_text_key(sentences)
    text_hit = history.lookup_page("text", text_key) if reuse_history else None
    match_stage = "text" if text_hit else ("none" if reuse_history else "disabled")
    source_version = text_hit["version_id"] if text_hit else None
    if text_hit:
        event("history_hit", stage="text")
    version_id = history.register_page(
        input_kind="text", input_sha=input_sha, image_key=None, ocr_key=None,
        text_key=text_key, sentences=sentences, source_version=source_version,
        match_stage=match_stage, hidden=hide_history, input_body=input_body,
    )
    history.record_run(
        input_kind="text", input_sha=input_sha, match_stage=match_stage,
        source_version=source_version, result_version=version_id,
        reuse=reuse_history, hidden=hide_history,
    )
    key = store.add(
        sentences, history_reuse=reuse_history, history_hidden=hide_history,
        history_version_id=version_id, history_match_stage=match_stage,
        history_text_key=text_key,
    )
    return {"page_id": key, "sentences": sentences,
            "history_version_id": version_id, "match_stage": match_stage,
            "skipped_stages": []}


def synthesize_chunk(text, seed=None, speed_factor=1.0):
    text = normalize_speech_text(text)
    # 切分策略来自配置；native 模式将完整朗读单元交给固定上游原生切句。
    payload = {
        "text": text,
        "text_lang": "zh",
        "ref_audio_path": settings["reference_audio"],
        "prompt_text": settings["reference_text"],
        "prompt_lang": settings.get("prompt_lang", "ja"),
        "text_split_method": settings.get("text_split_method", "cut2"),
        "batch_size": settings.get("tts_batch_size", 1),
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": settings.get("tts_parallel_infer", False),
        "speed_factor": speed_factor,
        "seed": settings.get("seed", 42) if seed is None else seed,
        "repetition_penalty": settings.get("repetition_penalty", 1.35),
        "top_k": settings.get("top_k", 15),
    }
    req = urllib.request.Request(
        settings["tts_url"] + "/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=240) as response:
        body = response.read()
    with wave.open(io.BytesIO(body), "rb") as wav:
        if wav.getnframes() == 0 or wav.getcomptype() != "NONE":
            raise ValueError("语音服务返回空音频或不支持的 WAV")
        if len(wav.readframes(wav.getnframes())) != wav.getnframes() * wav.getnchannels() * wav.getsampwidth():
            raise ValueError("语音服务返回截断 WAV")
    return body


def synthesize(text, audit=None, cancelled=None, asr_check=None, speed_factor=1.0):
    normalized = normalize_speech_text(text)
    guard_enabled = settings.get("speech_guard") == "all" if asr_check is None else bool(asr_check)
    chunks = ([normalized] if settings.get("speech_mode") == "native"
              else speech_chunks(text))
    chunks = [part for part in chunks if part.strip()]
    if not chunks:
        raise ValueError("没有可朗读的中文或数字")
    data = []
    event("tts_start", chunks=len(chunks), characters=len(text), speed_factor=speed_factor)
    event("tts_coverage_on" if guard_enabled else "tts_coverage_off")
    for number, chunk in enumerate(chunks, 1):
        if cancelled and cancelled():
            raise ValueError("页面已取消")
        started = time.monotonic()
        event("tts_chunk_start", chunk=number, chunks=len(chunks), characters=len(chunk))
        recovery = None
        if guard_enabled:
            def synthesize_at_speed(part, seed=None):
                return synthesize_chunk(part, seed=seed, speed_factor=speed_factor)
            body, recovery = guarded_synthesize(
                chunk, synthesize_at_speed, settings.get("seed", 42), cancelled
            )
        else:
            body = synthesize_chunk(chunk, speed_factor=speed_factor)
        event("tts_chunk_end", chunk=number, chunks=len(chunks), bytes=len(body), seconds=round(time.monotonic()-started, 3))
        data.append(body)
        if audit is not None:
            with wave.open(io.BytesIO(body), "rb") as wav:
                record = {"text": chunk, "characters": len(chunk), "audio_sha256": hashlib.sha256(body).hexdigest(), "frames": wav.getnframes(), "sample_rate": wav.getframerate(), "request_seconds": round(time.monotonic()-started, 3), "asr_checked": guard_enabled, "speed_factor": speed_factor}
                if recovery is not None:
                    record.update(recovery)
                audit.append(record)
    if cancelled and cancelled():
        raise ValueError("页面已取消")
    if len(data) == 1:
        return data[0]
    frames, fmt = [], None
    for body in data:
        with wave.open(io.BytesIO(body), "rb") as wav:
            current = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
            if fmt is not None and fmt != current:
                raise ValueError("分段音频格式不一致")
            fmt = current
            frames.append(wav.readframes(wav.getnframes()))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(fmt[0])
        wav.setsampwidth(fmt[1])
        wav.setframerate(fmt[2])
        wav.writeframes(b"".join(frames))
    return output.getvalue()


def audio_options(request):
    requested_guard = request.headers.get("X-Reader-ASR-Check")
    if requested_guard not in (None, "all", "off"):
        raise HTTPException(422, "X-Reader-ASR-Check 只能是 all 或 off")
    asr_check = settings.get("speech_guard") == "all" if requested_guard is None else requested_guard == "all"
    requested_speed = request.headers.get("X-Reader-Speech-Speed", "1.00")
    try:
        raw_speed = float(requested_speed)
    except (TypeError, ValueError):
        raise HTTPException(422, "X-Reader-Speech-Speed 必须是 0.50 到 1.50 的数字")
    if not math.isfinite(raw_speed) or not 0.50 <= raw_speed <= 1.50:
        raise HTTPException(422, "X-Reader-Speech-Speed 必须在 0.50 到 1.50 之间")
    speed_factor = round(raw_speed, 2)
    return asr_check, speed_factor


def consume_task_exception(done):
    if not done.cancelled():
        done.exception()


def select_prefetch_mode(page, asr_check, speed_factor):
    mode = (asr_check, speed_factor)
    if page.get("prefetch_mode") != mode:
        page["prefetch_mode"] = mode
        page["prefetch_generation"] = page.get("prefetch_generation", 0) + 1
    return page["prefetch_generation"]


def schedule_page_prefetch(key):
    page = store.get(key)
    if not page or page.get("prefetch_completed_generation") == page.get("prefetch_generation"):
        return None
    task = page_prefetch_tasks.get(key)
    if task is not None and not task.done():
        return task
    task = asyncio.create_task(prefetch_page_audio(key))
    page_prefetch_tasks[key] = task
    def finished(done):
        consume_task_exception(done)
        if page_prefetch_tasks.get(key) is done:
            page_prefetch_tasks.pop(key, None)
    task.add_done_callback(finished)
    return task


async def prefetch_page_audio(key):
    max_units = max(1, int(settings.get("full_page_prefetch_max_units", 24)))
    max_bytes = max(1, int(settings.get("full_page_prefetch_max_bytes", 64 * 1024 * 1024)))
    while True:
        p = store.get(key)
        if not p or not p.get("full_page_prefetch"):
            return
        generation = p.get("prefetch_generation", 0)
        asr_check, speed_factor = p["prefetch_mode"]
        total_bytes = 0
        for index in range(min(len(p["sentences"]), max_units)):
            p = store.get(key)
            if not p or p.get("prefetch_generation") != generation:
                break
            mode = (asr_check, speed_factor)
            if p["audio_modes"].get(index) == mode:
                total_bytes += len(p["audio"].get(index, b""))
                continue
            if total_bytes >= max_bytes:
                p["prefetch_completed_generation"] = generation
                event("page_prefetch_bounded", units=index, bytes=total_bytes)
                return
            try:
                body = await ensure_audio(
                    key, index, asr_check, speed_factor, "page_prefetch", generation
                )
            except (HTTPException, asyncio.CancelledError):
                if not store.get(key):
                    return
                break
            total_bytes += len(body)
        else:
            p["prefetch_completed_generation"] = generation
            event("page_prefetch_ready", units=min(len(p["sentences"]), max_units), bytes=total_bytes)
            return
        current = store.get(key)
        if not current or current.get("prefetch_generation") == generation:
            return


async def ensure_audio(key, index, asr_check, speed_factor, source="request", prefetch_generation=None):
    task_key = (key, index, asr_check, speed_factor)
    current = asyncio.current_task()
    existing = audio_tasks.get(task_key)
    if existing is not None and existing is not current:
        event("audio_task_shared", index=index)
        return await existing
    if existing is None:
        task = asyncio.create_task(ensure_audio(key, index, asr_check, speed_factor, source, prefetch_generation))
        audio_tasks[task_key] = task
        try:
            return await task
        finally:
            if audio_tasks.get(task_key) is task:
                audio_tasks.pop(task_key, None)
    p = store.get(key)
    if not p or index < 0 or index >= len(p["sentences"]):
        raise HTTPException(404, "页面已过期或句子不存在")
    audio_mode = (asr_check, speed_factor)
    if index in p["audio"] and p["audio_modes"].get(index) == audio_mode:
        return p["audio"][index]
    history_audio_key = audio_history_key(
        p["sentences"][index]["text"], asr_check, speed_factor
    )
    if p.get("history_reuse", False):
        cached = history.lookup_audio(history_audio_key)
        if cached is not None:
            p["audio"][index] = cached["body"]
            p["audio_modes"][index] = audio_mode
            p.setdefault("tts_trace", {})[index] = cached["audit"]
            p.setdefault("audio_history_versions", {})[index] = cached["version_id"]
            event("history_audio_hit", index=index)
            return cached["body"]
    event("audio_queued", index=index, source=source)
    async with tts_lock:
        current_page = store.get(key)
        if (source == "page_prefetch" and
                (not current_page or current_page.get("prefetch_generation") != prefetch_generation)):
            raise HTTPException(409, "预取参数已变化")
        if model_catalog.loaded_id != model_catalog.active_id:
            try:
                await asyncio.to_thread(model_catalog.ensure_loaded)
            except Exception as e:
                event("model_load_error", reason=str(e))
                raise HTTPException(502, str(e))
        if not store.get(key):
            raise HTTPException(410, "页面已取消")
        if p.get("history_reuse", False):
            cached = history.lookup_audio(history_audio_key)
            if cached is not None:
                p["audio"][index] = cached["body"]
                p["audio_modes"][index] = audio_mode
                p.setdefault("tts_trace", {})[index] = cached["audit"]
                p.setdefault("audio_history_versions", {})[index] = cached["version_id"]
                event("history_audio_hit", index=index)
                return cached["body"]
        if index not in p["audio"] or p["audio_modes"].get(index) != audio_mode:
            try:
                trace = []
                p["audio"][index] = await asyncio.to_thread(
                    synthesize, p["sentences"][index]["text"], audit=trace,
                    cancelled=lambda: store.get(key) is None, asr_check=asr_check,
                    speed_factor=speed_factor,
                )
                p["audio_modes"][index] = audio_mode
                p.setdefault("tts_trace", {})[index] = trace
                audio_version = history.register_audio(
                    history_audio_key, p["audio"][index], trace,
                    source_page_version=p.get("history_version_id"),
                    hidden=p.get("history_hidden", False),
                )
                p.setdefault("audio_history_versions", {})[index] = audio_version
            except Exception as e:
                event("tts_error", reason=str(e))
                if not store.get(key):
                    raise HTTPException(410, "页面已取消")
                raise HTTPException(502, "语音合成失败：" + str(e))
    if not store.get(key):
        raise HTTPException(410, "页面已取消")
    return p["audio"][index]


@app.get("/history")
def history_versions(request: Request, limit: int = 50):
    authorize(request)
    return history.list_versions(limit)


@app.get("/pages/{key}/audio/{index}")
async def audio(key: str, index: int, request: Request):
    authorize(request)
    asr_check, speed_factor = audio_options(request)
    p = store.get(key)
    if p and p.get("full_page_prefetch"):
        select_prefetch_mode(p, asr_check, speed_factor)
    body = await ensure_audio(key, index, asr_check, speed_factor)
    p = store.get(key)
    if not p:
        raise HTTPException(410, "页面已取消")
    # 调试留存与返回安卓的字节一致，默认关闭；按页面和句序归档。
    if settings.get("save_debug_pages", False):
        folder = ROOT / "~outputs-intermediate/pages" / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{index + 1:03}.wav").write_bytes(p["audio"][index])
        (folder / f"{index + 1:03}.tts.json").write_text(json.dumps(p.get("tts_trace", {}).get(index, []), ensure_ascii=False, indent=2), encoding="utf-8")
    event("audio_ready", index=index, bytes=len(body))
    if p.get("full_page_prefetch"):
        schedule_page_prefetch(key)
    return Response(body, media_type="audio/wav")


@app.delete("/pages/{key}")
def cancel(key: str, request: Request):
    authorize(request)
    store.cancel(key)
    event("page_cancelled")
    return {"cancelled": True}
