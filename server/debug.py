# 记录结构化事件；不记录图片、正文、请求地址或口令，TTS/模型失败例外保留完整异常原因。
import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / '~temp/logs/debug.jsonl'
_guard = threading.Lock()
_logger = None
FIELDS = {'bytes', 'rows', 'chunks', 'chunk', 'characters', 'seconds', 'status', 'index'}
REASON_EVENTS = {'tts_error', 'model_load_error', 'warmup_error'}

def event(name, **fields):
    global _logger
    try:
        with _guard:
            if _logger is None:
                LOG.parent.mkdir(parents=True, exist_ok=True)
                _logger = logging.getLogger('reader.debug.events')
                _logger.setLevel(logging.INFO)
                _logger.propagate = False
                handler = RotatingFileHandler(LOG, maxBytes=5_000_000, backupCount=3, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(message)s'))
                _logger.addHandler(handler)
            payload = {'time': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(), 'event': name}
            payload.update({k: v for k,v in fields.items() if k in FIELDS and isinstance(v, (int, float))})
            if name in REASON_EVENTS and isinstance(fields.get('reason'), str):
                payload['reason'] = ' '.join(fields['reason'].split())
            _logger.info(json.dumps(payload, ensure_ascii=False))
    except OSError:
        pass  # 调试输出不可用时不阻止朗读。
