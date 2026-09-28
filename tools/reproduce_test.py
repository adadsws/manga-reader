# 本机复现入口：复用已有采集/报告工具，状态与结果按批次隔离。
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import full_volume_run as capture
from tools import full_volume_report as reports

BASE = ROOT / '~outputs-intermediate/evidence/reproductions'
ACTIVE = ROOT / '~temp/reproduction/active.json'
HISTORICAL = ROOT / '~archive/20260928-历史验收证据与旧计划/docs/evidence/full-volume-20260920'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def session():
    if not ACTIVE.exists():
        raise RuntimeError('请先运行 01_new_session.bat。')
    state = read(ACTIVE)
    out = (ROOT / state['out']).resolve()
    if not out.is_relative_to(BASE.resolve()) or out == BASE.resolve():
        raise RuntimeError('批次路径超出本项目复现目录。')
    capture.OUT = reports.OUT = out
    capture.ALBUM = '/sdcard/Pictures/ReaderRepro' + state['id'].replace('-', '')
    source = (ROOT / state.get('source', capture.SOURCE)).resolve()
    if not source.is_dir():
        raise RuntimeError('本批次漫画目录不存在：' + str(source))
    capture.SOURCE = source
    return out, state


def ensure_device():
    names = capture.adb('emu', 'avd', 'name').decode().splitlines()
    name = names[0].strip() if names else capture.adb(
        'shell', 'getprop', 'ro.boot.qemu.avd_name'
    ).decode().strip()
    if name != 'ReaderAosp35':
        raise RuntimeError('仅允许本项目官方 ReaderAosp35。')
    if capture.adb('shell', 'getprop', 'sys.boot_completed').strip() != b'1':
        raise RuntimeError('安卓仍在启动，请稍后重试。')


def nodes():
    capture.adb('shell', 'uiautomator', 'dump', '/sdcard/reader-ui.xml')
    return list(capture.ET.fromstring(capture.adb('shell', 'cat', '/sdcard/reader-ui.xml')).iter('node'))


def projection_active():
    return b'org.local.reader' in capture.adb('shell', 'dumpsys', 'media_projection')


def media_volume():
    output = capture.adb('shell', 'cmd', 'media_session', 'volume', '--stream', '3', '--get').decode(errors='replace')
    match = re.search(r'volume is (\d+) in range \[0\.\.(\d+)\]', output)
    if not match:
        raise RuntimeError('无法读取安卓媒体音量。')
    return int(match.group(1)), int(match.group(2))


def set_media_volume(value):
    current, maximum = media_volume()
    target = max(0, min(int(value), maximum))
    for _ in range(maximum * 3 + 3):
        if current == target:
            return current
        key = '24' if target > current else '25'
        capture.adb('shell', 'input', 'keyevent', key)
        time.sleep(.15)
        current, _ = media_volume()
    raise RuntimeError(f'安卓媒体音量设置失败：目标 {target}，实际 {current}。')


def checkbox(label, value):
    n = None
    for _ in range(8):
        n = next((item for item in nodes() if item.get('text') == label), None)
        if n is not None:
            break
        capture.adb('shell', 'input', 'swipe', 540, 1500, 540, 520, 400)
        time.sleep(.4)
    if n is None:
        raise RuntimeError('没有找到设置控件：' + label)
    old = n.get('checked') == 'true'
    if old != value:
        capture.tap(label)
    return old


def open_advanced_options():
    # 高级项默认折叠；若目标控件尚未渲染，按稳定按钮文本展开一次。
    labels = {item.get('text') or '' for item in nodes()}
    if '收起高级选项' in labels or '下一页向左翻（默认向右翻）' in labels:
        return
    capture.tap('高级选项')
    time.sleep(.5)


def resolve_source(value=None):
    source = Path(value).expanduser() if value else capture.SOURCE
    if not source.is_absolute():
        source = ROOT / source
    return source.resolve()


def check(source=None):
    source = resolve_source(source)
    needed = [ROOT / 'config/runtime.json', ROOT / 'config/reader.json',
              capture.ADB, ROOT / '~temp/android-sdk/emulator/emulator.exe',
              ROOT / '~temp/avd/ReaderAosp35.ini', ROOT / 'reader.apk',
              ROOT / '~temp/ocrdeps', Path(read(ROOT / 'config/runtime.json')['python']),
              Path(read(ROOT / 'config/asr-validation.lock.json')['model_path'])]
    missing = [str(p) for p in needed if not p.exists()]
    pages = sorted(source.glob('*.jpg')) if source.is_dir() else []
    if not pages:
        missing.append('指定漫画目录没有 JPG：' + str(source))
    if missing:
        raise RuntimeError('缺少运行条件：\n' + '\n'.join(missing))
    for module in ('cv2', 'funasr', 'fastapi'):
        import importlib.util
        if importlib.util.find_spec(module) is None:
            raise RuntimeError('缺少本地依赖：' + module)
    print(f'本机路径与输入检查通过：{len(pages)} 页；没有解码或显示图片。', flush=True)
    print('漫画目录：', source, flush=True)
    print('复现结果：', BASE)
    return source, pages


def new(source=None):
    source, pages = check(source)
    if ACTIVE.exists():
        old_out, old = session()
        if not old.get('restored'):
            raise RuntimeError('上一批次尚未恢复配置，请先运行 06_restore.bat。')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    out = BASE / stamp
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ROOT / 'config/reader.json', out / 'reader-config-before.json')
    state = {'id': stamp, 'out': out.relative_to(ROOT).as_posix(), 'restored': False,
             'auto_before': False, 'left_before': False,
             'source': source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else str(source),
             'source_pages': len(pages)}
    save(ACTIVE, state)
    save(out / 'session.json', state)
    config = read(ROOT / 'config/reader.json')
    config['save_debug_pages'] = True
    save(ROOT / 'config/reader.json', config)
    print('新批次：', out)


def prepare():
    out, state = session()
    ensure_device()
    if state.get('restored') or (out / 'run.json').exists():
        raise RuntimeError('此批次已恢复或已经采集，请建立新批次。')
    capture.prepare()


def import_feedback():
    out, state = session()
    ensure_device()
    if state.get('restored') or (out / 'run.json').exists():
        raise RuntimeError('请建立新的反馈复测批次。')
    names = ['0006', '0007', '0010', '0012', '0014', '0017', '0023', '0024', '0027', '0033']
    capture.prepare(files=[capture.SOURCE / (name + '.jpg') for name in names])


def setup(optimizations='keep'):
    out, state = session()
    ensure_device()
    if state.get('restored') or (out / 'run.json').exists():
        raise RuntimeError('此批次不能再次开始采集。')
    sources = read(out / 'sources.json')
    if 'media_volume_before' not in state:
        state['media_volume_before'] = media_volume()[0]
        save(ACTIVE, state)
        save(out / 'session.json', state)
    set_media_volume(0)
    print(f'调试媒体音量已静音；完成后恢复为 {state["media_volume_before"]}。', flush=True)
    print('1/5 正在打开漫画朗读设置…', flush=True)
    capture.adb('shell', 'am', 'start', '-n', 'org.local.reader/.MainActivity')
    time.sleep(1)
    for _ in range(6):
        capture.adb('shell', 'input', 'swipe', 540, 520, 540, 1500, 250)
    time.sleep(.5)
    for label, desired, key in [('读完一页后自动翻页', True, 'auto_before')]:
        print('2/5 正在确认设置：' + label, flush=True)
        old = checkbox(label, desired)
        if not state.get('settings_saved'):
            state[key] = old
    open_advanced_options()
    label, desired, key = ('下一页向左翻（默认向右翻）', read(out / 'flip-calibration.json')['left'], 'left_before')
    print('2/5 正在确认设置：' + label, flush=True)
    old = checkbox(label, desired)
    if not state.get('settings_saved'):
        state[key] = old
    if optimizations != 'keep':
        desired = optimizations == 'on'
        for label, key in [('OCR 与分镜并行', 'parallel_vision_before'),
                           ('首段语音提前生成', 'eager_first_audio_before'),
                           ('按顺序预取本页全部语音', 'early_prefetch_before'),
                           ('翻页后自适应抓帧', 'adaptive_capture_before')]:
            print('2/5 正在确认低等待设置：' + label, flush=True)
            old = checkbox(label, desired)
            if key not in state:
                state[key] = old
        state['optimization_mode'] = optimizations
    state['settings_saved'] = True
    save(ACTIVE, state)
    save(out / 'session.json', state)
    if b'org.local.reader' not in capture.adb('shell', 'settings', 'get', 'secure', 'enabled_accessibility_services'):
        raise RuntimeError('请在专用模拟器的系统无障碍设置中开启漫画朗读，然后重试。')
    if projection_active():
        print('3/5 无障碍权限与现有整屏投屏均已确认。', flush=True)
    else:
        print('3/5 无障碍权限已确认，正在申请整个屏幕投屏…', flush=True)
        capture.tap('保存并启动朗读浮窗')
        time.sleep(1)
        labels = {n.get('text') for n in nodes()}
        if 'A single app' in labels:
            capture.tap('A single app')
            capture.tap('Entire screen')
        elif '单个应用' in labels:
            capture.tap('单个应用')
            capture.tap('整个屏幕')
        labels = {n.get('text') for n in nodes()}
        start = next((x for x in ('START', '立即开始', '开始') if x in labels), None)
        if start:
            capture.tap(start)
        for second in range(1, 11):
            if projection_active():
                break
            print(f'3/5 等待系统建立整屏投屏… {second}/10 秒', flush=True)
            time.sleep(1)
        if not projection_active():
            raise RuntimeError('投屏未启动；请在系统对话框选择 Entire screen / 整个屏幕并同意。')
    print('4/5 整屏投屏已启动，正在打开首个测试页面…', flush=True)
    capture.open_page(sources[0])
    windows = capture.adb('shell', 'dumpsys', 'window', 'windows').decode(errors='replace')
    for block in windows.split('  Window #'):
        if 'org.local.reader' in block and 'ty=APPLICATION_OVERLAY' in block:
            m = re.search(r' frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]', block)
            if m:
                x, y, r, b = map(int, m.groups())
                capture.adb('shell', 'input', 'swipe', (x+r)//2, y+20, 809, 449, 700)
    print('5/5 浮窗位置已设置，正在保存测试状态…', flush=True)
    state['ready'] = True
    save(ACTIVE, state)
    direction = '向左' if read(out / 'flip-calibration.json')['left'] else '向右'
    print(f'已按校准结果配置自动{direction}翻页、整屏投屏和首屏；尚未开始朗读。')


def copy_logs(out):
    for src, dest in [('reader.out.log', 'server-http.log'), ('reader.err.log', 'server-errors.log')]:
        p = ROOT / '~temp/logs' / src
        if p.exists() and not (out / dest).exists():
            shutil.copy2(p, out / dest)


def collect():
    out, state = session()
    ensure_device()
    if not state.get('ready') or state.get('restored'):
        raise RuntimeError('请先运行 03_android_setup.bat。')
    if not read(ROOT / 'config/reader.json')['save_debug_pages']:
        raise RuntimeError('截图留存未开启，请恢复本批次后重新新建批次。')
    if b'org.local.reader' not in capture.adb('shell', 'dumpsys', 'media_projection'):
        raise RuntimeError('投屏已经结束，请重新运行03。')
    items = read(out / 'sources.json')
    matcher = capture.Matcher(items)
    capture.open_page(items[0])
    p = out / 'start-check.png'
    p.write_bytes(capture.adb('exec-out', 'screencap', '-p'))
    match = matcher.match(p)
    if not match['accepted'] or match['source'] != items[0]['source']:
        raise RuntimeError('首屏数值匹配失败，尚未触发朗读。')
    save(out / 'start-verification.json', match)
    windows = capture.adb('shell', 'dumpsys', 'window', 'windows').decode(errors='replace')
    for block in windows.split('  Window #'):
        if 'org.local.reader' in block and 'ty=APPLICATION_OVERLAY' in block:
            m = re.search(r' frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]', block)
            if m:
                save(out / 'overlay-placement.json', {'rectangle': list(map(int, m.groups()))})
    try:
        capture.collect()
    finally:
        copy_logs(out)
    print('采集结束。请运行05生成报告，然后运行06恢复配置。')


def diagnostics(out):
    # 同步执行本轮回归与上游完整性检查，不沿用旧批次的通过结果。
    with (out / 'unit-tests.log').open('wb') as log:
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_reader*.py'], cwd=ROOT, stdout=log, stderr=log)
    lock = read(ROOT / 'config/upstream-files.lock.json')
    bad = []
    count = 0
    for group, files in lock.items():
        for name, expected in files.items():
            p = ROOT / 'reference' / group / name
            count += 1
            if not p.is_file() or capture.digest(p) != expected:
                bad.append((group + '/' + name))
    save(out / 'upstream-verification.json', {'checked_files': count, 'mismatches': bad})
    package = capture.adb('shell', 'pm', 'path', 'org.local.reader').decode().strip().split('package:')[-1]
    installed = capture.adb('shell', 'sha256sum', package).decode().split()[0]
    save(out / 'environment.json', {'avd': 'ReaderAosp35', 'api': capture.adb('shell', 'getprop', 'ro.build.version.sdk').decode().strip(),
         'installed_apk_sha256': installed, 'local_apk_sha256': capture.digest(ROOT / 'reader.apk'),
         'unit_tests_exit_code': result.returncode, 'manual_image_review_by_tool': False})
    overlay = {'status': 'not_assessed'}
    run = read(out / 'run.json')
    if run['records'] and (out / 'overlay-placement.json').exists():
        import cv2
        import numpy as np
        x, y, right, bottom = read(out / 'overlay-placement.json')['rectangle']
        first = out / 'pages' / run['records'][0]['page_id']
        def blue(path):
            image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)[y:bottom, x:right].astype(np.int16)
            b, g, r = cv2.split(image)
            return int(np.sum((b-r>25) & (g-r>10) & (b>45) & (r<65)))
        overlaps = []
        for unit in read(first / 'ocr.json'):
            for segment in unit.get('segments', []):
                X, Y, R, B = segment['box']
                area = max(0, min(right, R)-max(x, X))*max(0, min(bottom, B)-max(y, Y))
                if area: overlaps.append({'box': segment['box'], 'overlap_area': area})
        overlay = {'status': 'measured', 'overlay_rectangle': [x, y, right, bottom],
                   'recognized_boxes_intersecting_overlay': overlaps,
                   'before_blue_pixels_in_rectangle': blue(out / 'overlay-before.png'),
                   'upload_blue_pixels_in_rectangle': blue(first / 'screen.jpg'),
                   'manual_image_review': False}
    sources = read(out / 'sources.json')
    import wave
    audio_checks = []
    for rec in run['records']:
        folder = out / 'pages' / rec['page_id']
        units = reports.audio_units(folder)
        unit_checks = []
        for unit in units:
            audio = folder / unit['audio']
            trace = folder / unit['trace'] if unit['trace'] else None
            raw = capture.RAW / rec['page_id'] / f"{unit['index']:03d}.wav"
            check = {'index': unit['index'], 'audio': unit['audio'], 'sha256': unit['sha256'],
                     'matches_server_response_bytes': raw.exists() and capture.digest(audio) == capture.digest(raw),
                     'trace_present': bool(trace and trace.exists())}
            if trace and trace.exists():
                with wave.open(str(audio), 'rb') as wav:
                    check['chunk_frames_match_complete_wav'] = sum(c['frames'] for c in read(trace)) == wav.getnframes()
            unit_checks.append(check)
        item = {'source': rec['source'], 'expected_units': rec['units'], 'audio_units_present': len(units),
                'all_units_present': len(units) == rec['units'], 'units': unit_checks,
                'matches_server_response_bytes': bool(unit_checks) and all(x['matches_server_response_bytes'] for x in unit_checks),
                'chunk_frames_match_complete_wav': bool(unit_checks) and all(x.get('chunk_frames_match_complete_wav', False) for x in unit_checks)}
        page_audio = folder / 'page.wav'
        if units and not units[0]['legacy_page_audio'] and len(units) == rec['units']:
            capture.concatenate_wavs([folder / x['audio'] for x in units], page_audio)
        item['page_audio_present'] = page_audio.exists()
        if page_audio.exists() and units:
            with wave.open(str(page_audio), 'rb') as joined:
                item['page_audio_frames'] = joined.getnframes()
                item['page_audio_matches_unit_frames'] = joined.getnframes() == sum(x['frames'] for x in units)
                item['page_audio_sha256'] = capture.digest(page_audio)
        audio_checks.append(item)
    save(out / 'final-checks.json', {
        'source_hashes_match': all((ROOT / item['relative_path']).is_file() and capture.digest(ROOT / item['relative_path']) == item['sha256'] for item in sources),
        'capture_order_matches_sources': [r['source'] for r in run['records']] == [p['source'] for p in sources],
        'completed_events': sum(e['type'] == 'completed' for e in run['events']),
        'expected_pages': len(sources), 'audio': audio_checks,
        'apk_matches': installed == capture.digest(ROOT / 'reader.apk'),
        'upstream_mismatches': bad, 'unit_tests_exit_code': result.returncode,
    })
    save(out / 'overlay-verification.json', overlay)
    if (out / 'end.png').exists():
        save(out / 'end-page-verification.json', capture.Matcher(read(out / 'sources.json')).match(out / 'end.png'))
    else:
        save(out / 'end-page-verification.json', {'status': 'not_available'})


def normalize_run_event_names(out):
    run = read(out / 'run.json')
    changed = False
    for event in run.get('events', []):
        if event.get('type') == 'duplicate':
            event['type'] = 'page_unchanged'
            changed = True
    if run.get('terminal', {}).get('type') == 'duplicate':
        run['terminal']['type'] = 'page_unchanged'
        changed = True
    for intervention in run.get('interventions', []):
        if intervention.get('reason') == 'duplicate':
            intervention['reason'] = 'page_unchanged'
            changed = True
    if changed:
        save(out / 'run.json', run)


def report():
    out, state = session()
    ensure_device()
    if not (out / 'run.json').exists():
        raise RuntimeError('没有采集结果，请先运行04。')
    normalize_run_event_names(out)
    diagnostics(out)
    checks = read(out / 'final-checks.json')
    reports.asr(watch=False)
    # 报告先生成，再把链接和WAV检查结果写回，最后更新证据哈希。
    if not (out / 'restoration.json').exists():
        save(out / 'restoration.json', {'status': 'not_restored', 'configuration_bytes_restored': False})
    reports.report()
    manifest = read(out / 'manifest.json')
    checks.update({'source_pages': len(read(out / 'sources.json')), 'completed_pages': manifest['summary']['completed_pages'],
              'all_capture_sources_in_order': [x['source'] for x in manifest['pages']] == [x['source'] for x in read(out / 'sources.json')],
              'wav_hashes_valid': all(reports.sha(out / 'pages' / p['page_id'] / unit['audio']) == unit['sha256']
                                      for p in manifest['pages'] for unit in p.get('audio_units', [])),
              'all_audio_units_present': all(p.get('audio_complete') for p in manifest['pages'] if p.get('units')),
              'page_wav_hashes_valid': all(reports.sha(out / 'pages' / p['page_id'] / p['page_audio']['audio']) == p['page_audio']['sha256']
                                           for p in manifest['pages'] if p.get('page_audio')),
              'all_page_wavs_match_unit_frames': all(x.get('page_audio_matches_unit_frames') for x in checks['audio'] if x.get('expected_units'))})
    save(out / 'final-checks.json', checks)
    reports.report()
    print('报告：', out / 'index.html')


def restore_files():
    out, state = session()
    copy_logs(out)
    shutil.copy2(out / 'reader-config-before.json', ROOT / 'config/reader.json')
    print('电脑原配置已还原；随后等待服务重启。')


def restore_android():
    out, state = session()
    ensure_device()
    if b'org.local.reader' in capture.adb('shell', 'dumpsys', 'media_projection'):
        capture.tap('×')
    # 关闭投屏后 Activity 可能仍保留在无法可靠复位的滚动位置；冷启动设置页再恢复控件。
    capture.adb('shell', 'am', 'force-stop', 'org.local.reader')
    capture.adb('shell', 'am', 'start', '-n', 'org.local.reader/.MainActivity')
    time.sleep(1)
    checkbox('读完一页后自动翻页', state['auto_before'])
    open_advanced_options()
    checkbox('下一页向左翻（默认向右翻）', state['left_before'])
    for label, key in [('OCR 与分镜并行', 'parallel_vision_before'),
                       ('首段语音提前生成', 'eager_first_audio_before'),
                       ('按顺序预取本页全部语音', 'early_prefetch_before'),
                       ('翻页后自适应抓帧', 'adaptive_capture_before')]:
        if key in state:
            checkbox(label, state[key])
    # checkbox() scrolls the settings page while locating controls.  The
    # connection status lives at the top and uiautomator only exposes the
    # currently rendered ScrollView children, so return to the top before
    # checking it.
    for _ in range(6):
        capture.adb('shell', 'input', 'swipe', 540, 520, 540, 1500, 250)
    time.sleep(.5)
    print('等待安卓自动连接状态确认…', flush=True)
    connected = False
    for _ in range(10):
        labels = {n.get('text') or '' for n in nodes()}
        if any(label.endswith('电脑已连接') or label.endswith('电脑服务已连接') for label in labels):
            connected = True
            break
        time.sleep(1)
    if not connected:
        raise RuntimeError('安卓连接检测未通过；电脑配置已恢复，请检查网络后重试06。')
    if 'media_volume_before' in state:
        set_media_volume(state['media_volume_before'])
    state['restored'] = True
    save(ACTIVE, state)
    save(out / 'session.json', state)
    save(out / 'restoration.json', {'configuration_bytes_restored': (ROOT / 'config/reader.json').read_bytes() == (out / 'reader-config-before.json').read_bytes(),
         'save_debug_pages': read(ROOT / 'config/reader.json')['save_debug_pages'], 'auto_flip': state['auto_before'],
         'android_connection_check': '电脑已连接', 'reader_projection_active': False, 'restart_retry_succeeded': True,
         'media_volume_restored': media_volume()[0],
         'note': '由复现入口恢复原始配置，未沿用历史批次的端口冲突结论。'})
    if (out / 'manifest.json').exists():
        reports.report()
    print('原配置已恢复，安卓连接正常。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'new', 'prepare', 'feedback-import', 'setup', 'collect', 'report', 'restore-files', 'restore-android', 'status', 'open', 'stop'])
    parser.add_argument('--source', help='新建批次使用的 JPG 目录；后续步骤从批次状态读取。')
    parser.add_argument('--optimizations', choices=['keep', 'on', 'off'], default='keep', help='setup 时统一设置四个 Android 低等待开关。')
    args = parser.parse_args()
    actions = {'check': check, 'new': new, 'prepare': prepare, 'feedback-import': import_feedback, 'setup': setup, 'collect': collect, 'report': report,
               'restore-files': restore_files, 'restore-android': restore_android}
    if args.action in actions:
        if args.action in ('check', 'new'):
            actions[args.action](args.source)
        elif args.action == 'setup':
            actions[args.action](args.optimizations)
        else:
            actions[args.action]()
    elif args.action == 'stop':
        out, state = session()
        save(out / 'stop-requested.json', {'requested_utc': datetime.now(timezone.utc).isoformat()})
        ensure_device()
        capture.tap('停')
        print('已请求停止采集和当前朗读；请等04结束后运行05/06。')
    else:
        out, state = session()
        if args.action == 'open':
            if not (out / 'index.html').exists():
                raise RuntimeError('报告尚未生成，请先运行05。')
            os.startfile(out / 'index.html')
        else:
            print('当前批次：', out, '\n已恢复配置：', state['restored'])
            if (out / 'run.json').exists():
                r = read(out / 'run.json')
                print('已匹配页数：', r['covered'], '/', r['expected_pages'], '采集结束：', r['finished'])


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('未完成：', str(error) or '用户中止。', file=sys.stderr)
        print('已有证据保留；环境恢复请运行06_restore.bat。', file=sys.stderr)
        sys.exit(1)
