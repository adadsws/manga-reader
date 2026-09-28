# 复现编排的隔离回归：只操作临时测试目录，不启动设备或服务。
import io
import json
import sys
import types
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

from tools import reproduce_test as kit
from tools import full_volume_report as reports


class ReproductionTests(unittest.TestCase):
    def setUp(self):
        self.folder = kit.ROOT / '~temp/reproduction-tests' / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.root = self.folder
        self.base = self.root / '~outputs-intermediate/evidence/reproductions'
        self.active = self.root / '~temp/reproduction/active.json'
        self.source = self.root / 'secrets/manga'
        self.source.mkdir(parents=True)
        self.patches = [patch.object(kit, 'ROOT', self.root), patch.object(kit, 'BASE', self.base),
                        patch.object(kit, 'ACTIVE', self.active),
                        patch.object(kit, 'check', return_value=(self.source, [self.source / '0001.jpg'])),
                        patch.object(kit.capture, 'OUT', self.root), patch.object(reports, 'OUT', self.root),
                        patch.object(kit.capture, 'ALBUM', '/sdcard/Pictures/Test')]
        for p in self.patches:
            p.start()
        kit.save(self.root / 'config/reader.json', {'save_debug_pages': False, 'test_value': 123})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        # 保留可重建临时文件，不隐式物理删除。

    def test_new_session_backups_exact_bytes_and_rejects_unrestored(self):
        before = (self.root / 'config/reader.json').read_bytes()
        kit.new()
        out, state = kit.session()
        self.assertEqual((out / 'reader-config-before.json').read_bytes(), before)
        self.assertTrue(kit.read(self.root / 'config/reader.json')['save_debug_pages'])
        with self.assertRaises(RuntimeError):
            kit.new()
        self.assertEqual(kit.read(self.active)['id'], state['id'])

    def test_new_session_persists_selected_source_and_page_count(self):
        kit.new(self.source)
        out, state = kit.session()
        self.assertEqual(state['source'], self.source.relative_to(self.root).as_posix())
        self.assertEqual(state['source_pages'], 1)
        self.assertEqual(kit.capture.SOURCE, self.source.resolve())
        self.assertEqual(kit.read(out / 'session.json')['source_pages'], 1)

    def test_legacy_duplicate_event_is_normalized_to_page_unchanged(self):
        out = self.base / 'normalize-case'
        out.mkdir(parents=True)
        kit.save(out / 'run.json', {'events': [{'type': 'duplicate'}],
                                    'terminal': {'type': 'duplicate'},
                                    'interventions': [{'reason': 'duplicate'}]})
        kit.normalize_run_event_names(out)
        run = kit.read(out / 'run.json')
        self.assertEqual(run['events'][0]['type'], 'page_unchanged')
        self.assertEqual(run['terminal']['type'], 'page_unchanged')
        self.assertEqual(run['interventions'][0]['reason'], 'page_unchanged')

    def test_media_volume_retries_when_android_coalesces_key_events(self):
        states = [(0, 15), (0, 15), (1, 15), (2, 15)]
        with patch.object(kit, 'media_volume', side_effect=states), \
                patch.object(kit.capture, 'adb') as adb, patch.object(kit.time, 'sleep'):
            self.assertEqual(kit.set_media_volume(2), 2)
        self.assertEqual(adb.call_count, 3)

    def test_checkbox_scrolls_until_control_is_visible(self):
        node = {'text': '目标开关', 'checked': 'false'}
        with patch.object(kit, 'nodes', side_effect=[[], [node]]), \
                patch.object(kit.capture, 'adb') as adb, \
                patch.object(kit.capture, 'tap') as tap, patch.object(kit.time, 'sleep'):
            self.assertFalse(kit.checkbox('目标开关', True))
        adb.assert_called_once()
        tap.assert_called_once_with('目标开关')

    def test_flip_calibration_selects_only_direction_reaching_second_page(self):
        items = [{'source': '0001.jpg'}, {'source': '0002.jpg'}]
        class FakeMatcher:
            def __init__(self, values): pass
            def match(self, path):
                source = '0002.jpg' if path.name.endswith('left.png') else '0001.jpg'
                return {'source': source, 'accepted': True, 'good_matches': 50, 'runner_up_matches': 2}
        def adb(*args):
            return b'Physical size: 1080x1920' if args[:3] == ('shell', 'wm', 'size') else b'png'
        with patch.object(kit.capture, 'Matcher', FakeMatcher), \
                patch.object(kit.capture, 'adb', side_effect=adb), \
                patch.object(kit.capture, 'open_page'), patch.object(kit.capture.time, 'sleep'):
            result = kit.capture.calibrate_flip(items)
        self.assertTrue(result['left'])
        self.assertEqual(kit.read(self.root / 'flip-calibration.json')['status'], 'passed')

    def test_restored_session_gets_distinct_next_directory(self):
        kit.new()
        first, state = kit.session()
        state['restored'] = True
        kit.save(self.active, state)
        kit.new()
        second, _ = kit.session()
        self.assertNotEqual(first, second)
        self.assertTrue((first / 'session.json').exists())

    def test_rejects_out_of_scope_state_path(self):
        kit.save(self.active, {'id': 'test', 'out': '../outside'})
        with self.assertRaises(RuntimeError):
            kit.session()

    def test_restores_original_configuration_exactly(self):
        before = (self.root / 'config/reader.json').read_bytes()
        kit.new()
        kit.restore_files()
        self.assertEqual((self.root / 'config/reader.json').read_bytes(), before)

    def test_restore_does_not_overwrite_capture_http_log(self):
        kit.new()
        out, _ = kit.session()
        (out / 'server-http.log').write_text('original run', encoding='utf-8')
        log = self.root / '~temp/logs/reader.out.log'
        log.parent.mkdir(parents=True)
        log.write_text('later restart', encoding='utf-8')
        kit.copy_logs(out)
        self.assertEqual((out / 'server-http.log').read_text(encoding='utf-8'), 'original run')

    def test_stop_request_ends_collector_and_keeps_partial_state(self):
        raw = self.root / 'raw'
        raw.mkdir()
        kit.save(self.root / 'sources.json', [{'source': '0003.jpg'}])
        def adb(*args):
            return b'org.local.reader' if 'enabled_accessibility_services' in args else b''
        def tap(label):
            kit.save(self.root / 'stop-requested.json', {'requested': True})
        with patch.object(kit.capture, 'RAW', raw), patch.object(kit.capture, 'Matcher'), \
                patch.object(kit.capture, 'adb', side_effect=adb), patch.object(kit.capture, 'tap', side_effect=tap):
            with patch.object(kit.capture.urllib.request, 'urlopen', return_value=io.BytesIO(b'{}')):
                kit.capture.collect()
        state = kit.read(self.root / 'run.json')
        self.assertTrue(state['stopped_by_user'])
        self.assertFalse(state['finished'])
        self.assertEqual(state['covered'], 0)

    def test_partial_asr_returns_without_waiting_for_more_pages(self):
        kit.save(self.root / 'run.json', {'records': [], 'finished': False})
        fake = types.ModuleType('funasr')
        fake.AutoModel = lambda **kwargs: object()
        with patch.dict(sys.modules, {'funasr': fake}), patch.object(reports, 'ROOT', self.root):
            kit.save(self.root / 'config/asr-validation.lock.json', {'model_path': 'unused'})
            reports.asr(watch=False)
        self.assertTrue((self.root / 'asr/runtime.log').exists())

    def write_wav(self, path, frames=160, sample=b'\0\0'):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(sample * frames)

    def test_audio_units_discovers_every_unit_and_legacy_fallback(self):
        folder = self.root / 'page'
        self.write_wav(folder / 'units/001.wav', 160)
        self.write_wav(folder / 'units/002.wav', 320)
        kit.save(folder / 'units/001.tts.json', [{'frames': 160}])
        units = reports.audio_units(folder)
        self.assertEqual([x['index'] for x in units], [1, 2])
        self.assertEqual([x['frames'] for x in units], [160, 320])
        self.assertEqual(units[0]['trace'], 'units/001.tts.json')
        self.assertFalse(units[0]['legacy_page_audio'])

    def test_concatenate_wavs_preserves_unit_order_and_frame_count(self):
        folder = self.root / 'joined-page'
        first = folder / 'units/001.wav'
        second = folder / 'units/002.wav'
        self.write_wav(first, 3, b'\x01\0')
        self.write_wav(second, 2, b'\x02\0')
        metadata = kit.capture.concatenate_wavs([first, second], folder / 'page.wav')
        with wave.open(str(folder / 'page.wav'), 'rb') as joined:
            frames = joined.readframes(joined.getnframes())
            self.assertEqual(joined.getnframes(), 5)
        self.assertEqual(frames, b'\x01\0' * 3 + b'\x02\0' * 2)
        self.assertEqual(metadata['source_units'], 2)
        self.assertEqual(metadata['frames'], 5)

    def test_tts_validation_summarizes_real_retry_and_recovery_fields(self):
        folder = self.root / 'trace-page'
        self.write_wav(folder / 'units/001.wav')
        self.write_wav(folder / 'units/002.wav')
        kit.save(folder / 'units/001.tts.json', [{'asr_checked': True, 'verified': True, 'recovered': True,
                                                   'attempts': [{}, {}, {}], 'requests': [{}, {}]}])
        kit.save(folder / 'units/002.tts.json', [{'asr_checked': True, 'verified': False, 'recovered': False,
                                                   'attempts': [{}], 'requests': [{}]}])
        summary = reports.tts_validation(folder, reports.audio_units(folder))
        self.assertEqual(summary['asr_checked_units'], 2)
        self.assertEqual(summary['recovered_units'], 1)
        self.assertEqual(summary['internal_retries'], 2)
        self.assertEqual(summary['total_requests'], 3)

    def test_asr_processes_all_audio_units_and_aggregates_page(self):
        page_id = 'test-page'
        folder = self.root / 'pages' / page_id
        self.write_wav(folder / 'units/001.wav')
        self.write_wav(folder / 'units/002.wav')
        kit.save(folder / 'ocr.json', [{'text': '甲'}, {'text': '乙'}])
        kit.save(self.root / 'run.json', {'records': [{'page_id': page_id, 'source': '0001.jpg', 'units': 2}],
                                                'finished': True, 'expected_pages': 1})
        kit.save(self.root / 'config/asr-validation.lock.json', {'model_path': 'unused'})
        calls = []
        class FakeModel:
            def generate(self, input):
                calls.append(Path(input).name)
                return [{'text': '甲' if input.endswith('001.wav') else '乙'}]
        fake = types.ModuleType('funasr')
        fake.AutoModel = lambda **kwargs: FakeModel()
        fake_core = types.ModuleType('server.core')
        fake_core.normalize_speech_text = lambda text: text
        with patch.dict(sys.modules, {'funasr': fake, 'server.core': fake_core}), patch.object(reports, 'ROOT', self.root):
            reports.asr(watch=False)
        summary = kit.read(self.root / 'asr/summary.json')
        self.assertEqual(calls, ['001.wav', '002.wav'])
        self.assertEqual(summary[0]['audio_unit_count'], 2)
        self.assertTrue(summary[0]['audio_complete'])
        self.assertEqual(summary[0]['aligned_character_fraction'], 1.0)

    def test_report_keeps_diagnostic_evidence(self):
        out = self.base / 'report-case'
        out.mkdir(parents=True)
        kit.save(out / 'run.json', {})
        kit.save(out / 'sources.json', [])
        def diagnostics(folder):
            kit.save(folder / 'final-checks.json', {'audio': [{'chunk_frames_match_complete_wav': True}]})
        def report():
            kit.save(out / 'manifest.json', {'summary': {'completed_pages': 0}, 'pages': []})
        with patch.object(kit, 'session', return_value=(out, {})), patch.object(kit, 'ensure_device'), patch.object(kit, 'diagnostics', side_effect=diagnostics), patch.object(reports, 'asr'), patch.object(reports, 'report', side_effect=report):
            kit.report()
        checks = kit.read(out / 'final-checks.json')
        self.assertTrue(checks['audio'][0]['chunk_frames_match_complete_wav'])
        self.assertTrue(checks['all_capture_sources_in_order'])


if __name__ == '__main__':
    unittest.main()
