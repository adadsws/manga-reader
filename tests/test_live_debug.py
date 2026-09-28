# 实时监视只接受固定事件/数值，覆盖截断、轮转和分批 UTF-8 写入。
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from tools.live_debug import JsonTail, format_event, android_event, AndroidStream
from server.debug import FIELDS

class LiveDebugTests(unittest.TestCase):
    def test_unknown_fields_and_text_are_not_displayed(self):
        data={'event':'tts_start','chunks':2,'characters':9,'text':'PRIVATE_BODY','token':'PRIVATE_TOKEN','seconds':'PRIVATE_ERROR'}
        value=format_event(data)
        self.assertIn('总段数=2',value)
        self.assertNotIn('PRIVATE',value)
        self.assertIsNone(format_event({'event':'PRIVATE'}))
        self.assertIsNone(format_event({'event':[]}))
        self.assertNotIn('text',FIELDS)
        self.assertNotIn('token',FIELDS)
        failure=format_event({'event':'tts_error','reason':'CUDA out of memory：完整诊断'})
        self.assertIn('完整原因=CUDA out of memory：完整诊断',failure)
        self.assertIn('继续朗读',format_event({'event':'tts_coverage_unverified'}))
        self.assertIn('已保留通过片段',format_event({'event':'tts_recovery_partial'}))
        self.assertIn('启动预热全部完成',format_event({'event':'warmup_ready','seconds':12.3}))
        self.assertNotIn('不应显示',format_event({'event':'tts_start','reason':'不应显示'}))

    def test_android_tag_payload_is_strict(self):
        self.assertIn('开始播放',android_event('event=play_start generation=3 auto=false'))
        self.assertIn('画面未变化',android_event('event=page_unchanged generation=4 auto=false'))
        for line in ['1/1 PRIVATE_BODY','event=play_start generation=3 auto=false PRIVATE','event=unknown generation=3 auto=true']:
            self.assertIsNone(android_event(line))

    def test_partial_lines_truncation_and_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'debug.jsonl'; tail=JsonTail(p)
            self.assertEqual(tail.read(),[])
            first=json.dumps({'event':'request_end','status':200,'text':'私有正文'},ensure_ascii=False).encode('utf-8')+b'\n'
            split=first.index('私'.encode('utf-8'))+1
            p.write_bytes(first[:split]);self.assertEqual(tail.read(),[])
            with p.open('ab') as f:f.write(first[split:])
            self.assertEqual(len(tail.read()),1); self.assertEqual(tail.read(),[])
            p.write_bytes(b'{"event":"ocr_error"}\n');self.assertEqual(tail.read(),['识别失败'])
            p.rename(Path(temp)/'old.jsonl');p.write_bytes(b'{"event":"request_start"}\n')
            self.assertEqual(tail.read(),['收到请求'])

    def test_malformed_lines_dont_stop_monitor(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'debug.jsonl';p.write_bytes(b'bad json\n[]\n{"event":"audio_ready","bytes":32}\n')
            self.assertEqual(JsonTail(p).read(),['整页音频已返回 | 字节=32'])

    def test_live_mode_skips_old_history(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'debug.jsonl';p.write_bytes(b'{"event":"ocr_error"}\n')
            tail=JsonTail(p,history=False);self.assertEqual(tail.read(),[])
            with p.open('ab') as f:f.write(b'{"event":"ocr_start"}\n')
            self.assertEqual(tail.read(),['正在识别与排序'])

    def test_android_stream_boundary_deduplicates_only_same_event(self):
        stream=AndroidStream('unused');stream.cursor='100.000000'
        line='100.100000 12 12 I ReaderDebug: event=auto_on generation=1 auto=true'
        self.assertIn('已开启',stream.parse(line))
        self.assertIsNone(stream.parse(line))
        self.assertIn('已关闭',stream.parse(line.replace('auto_on','auto_off').replace('auto=true','auto=false')))
        self.assertIsNone(stream.parse(line.replace('100.100000','99.100000')))
