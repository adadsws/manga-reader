# 全册实测编排：仅输出元数据，不向操作者展示图像、OCR 或转写正文。
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
import wave
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '~outputs-intermediate/evidence/full-volume-20260920'
RAW = ROOT / '~outputs-intermediate/pages'
ADB = ROOT / '~temp/android-sdk/platform-tools/adb.exe'
SOURCE = ROOT / 'secrets/manga/full-volume'
ALBUM = '/sdcard/Pictures/ReaderVolume20260920'


def adb(*args):
    return subprocess.check_output([str(ADB), '-s', 'emulator-5554', *map(str, args)], timeout=45)


def avd_name():
    names = adb('emu', 'avd', 'name').decode().splitlines()
    return names[0].strip() if names else adb(
        'shell', 'getprop', 'ro.boot.qemu.avd_name'
    ).decode().strip()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def concatenate_wavs(sources, destination):
    # 只拼接电脑实际返回的同格式 PCM 帧，不做重采样或重新合成。
    sources=[Path(p) for p in sources]
    if not sources:
        return None
    destination=Path(destination)
    temporary=destination.with_name(destination.name+'.tmp')
    expected_format=None
    total_frames=0
    with wave.open(str(temporary),'wb') as output:
        for source in sources:
            with wave.open(str(source),'rb') as audio:
                current=(audio.getnchannels(),audio.getsampwidth(),audio.getframerate(),audio.getcomptype())
                if expected_format is None:
                    expected_format=current
                    output.setnchannels(current[0]);output.setsampwidth(current[1]);output.setframerate(current[2])
                    output.setcomptype(current[3],'not compressed')
                if current!=expected_format:
                    raise ValueError(f'Audio format mismatch: {source}')
                frames=audio.getnframes()
                output.writeframes(audio.readframes(frames))
                total_frames+=frames
    temporary.replace(destination)
    return {'audio':'page.wav','frames':total_frames,'seconds':round(total_frames/expected_format[2],3),
            'sample_rate':expected_format[2],'channels':expected_format[0],'sample_width':expected_format[1],
            'sha256':digest(destination),'source_units':len(sources)}


def tap(text):
    # 设置页内容可能长于屏幕；步骤编号会随设置项增减，按稳定语义后缀定位。
    overlay_description = {'读': '重新开始', '停': '全部停止', '×': '关闭浮窗'}.get(text)
    size=adb('shell','wm','size').decode(errors='replace')
    match=re.search(r'(\d+)x(\d+)',size)
    width,height=map(int,match.groups()) if match else (1080,1920)
    for _ in range(6):
        adb('shell', 'uiautomator', 'dump', '/sdcard/reader-ui.xml')
        nodes = ET.fromstring(adb('shell', 'cat', '/sdcard/reader-ui.xml')).iter('node')
        for n in nodes:
            label=n.get('text') or ''
            description=n.get('content-desc') or ''
            if label == text or label.endswith(text) or (overlay_description and description == overlay_description):
                x,y,r,b = map(int, re.findall(r'\d+', n.get('bounds')))
                px,py=(x+r)//2,(y+b)//2
                if py > height-160:
                    adb('shell','input','swipe',width//2,int(height*.78),width//2,int(height*.3),500)
                    time.sleep(.5)
                    break
                adb('shell', 'input', 'tap', px, py)
                return [px,py]
        else:
            if text in ('读', '停', '×'):
                break
            adb('shell','input','swipe',width//2,int(height*.78),width//2,int(height*.3),500)
            time.sleep(.5)
    # Gallery 的无障碍树可能省略非聚焦浮窗；复用正式布局代码与窗口实际边界。
    if text in ('读', '停', '×'):
        windows=adb('shell','dumpsys','window','windows').decode('utf-8',errors='replace')
        for block in windows.split('  Window #'):
            if 'org.local.reader' in block and 'ty=APPLICATION_OVERLAY' in block and 'isVisible=true' in block:
                m=re.search(r' frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]',block)
                if m:
                    x,y,r,b=map(int,m.groups())
                    # 两个带小字的主按钮等宽，纯图标关闭按钮为其 55%。
                    center={'读':.5/2.55,'停':.5/2.55,'×':2.275/2.55}[text]
                    if text == '停': center=1.5/2.55
                    px=round(x+12+(r-x-24)*center);py=b-54
                    adb('shell','input','tap',px,py)
                    return [px,py]
    raise RuntimeError('UI control not found: ' + text)


def open_page(item):
    # Gallery may keep a stale activity/page when a new VIEW intent is sent
    # immediately after calibration or a previous batch.  Starting from a
    # stopped task makes the requested media id authoritative.
    adb('shell', 'am', 'force-stop', 'com.android.gallery3d')
    adb('shell', 'am', 'start', '-a', 'android.intent.action.VIEW', '-d',
        'content://media/external/images/media/' + str(item['media_id']), '-t', 'image/jpeg', '-n', 'com.android.gallery3d/.app.GalleryActivity')
    time.sleep(5)


def prepare(files=None):
    OUT.mkdir(parents=True, exist_ok=True)
    assert avd_name() == 'ReaderAosp35'
    if files is None:
        files = sorted(SOURCE.glob('*.jpg'))
        if not files:
            raise RuntimeError(f'指定目录没有 JPG：{SOURCE}')
    assert files and all(p.is_file() and p.parent == SOURCE for p in files)
    adb('shell', 'mkdir', '-p', ALBUM)
    items=[]
    for i,p in enumerate(files):
        remote = ALBUM + '/' + p.name
        adb('push', p, remote)
        # 系统相册按日期降序；首张设为最旧，使应用默认向右滑动进入下一编号。
        stamp = 1790000000 + i * 120
        adb('shell', 'touch', '-m', '-t', time.strftime('%Y%m%d%H%M.%S', time.gmtime(stamp)), remote)
        adb('shell', 'am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE', '-d', 'file://' + remote)
        items.append({'source':p.name, 'relative_path':p.relative_to(ROOT).as_posix(), 'bytes':p.stat().st_size, 'sha256':digest(p), 'remote':remote})
        print('imported', len(items), '/', len(files), flush=True)
    time.sleep(3)
    rows=adb('shell', 'content', 'query', '--uri', 'content://media/external/images/media', '--projection', '_id:_data:date_modified').decode('utf-8')
    (OUT/'media-index.txt').write_text(rows, encoding='utf-8')
    for item in items:
        matches=[line for line in rows.splitlines() if '_data=' + item['remote'].replace('/sdcard/', '/storage/emulated/0/') + ',' in line]
        assert len(matches)==1, item['source']
        item['media_id']=int(re.search(r'_id=(\d+)', matches[0]).group(1))
        actual=adb('shell', 'sha256sum', item['remote']).decode().split()[0]
        assert actual==item['sha256'], item['source']
        item['device_sha256']=actual
    save('sources.json', items)
    (OUT/'device.txt').write_bytes(adb('shell','getprop'))
    calibrate_flip(items)
    print('prepared',len(items),flush=True)


def calibrate_flip(items):
    # 用与正式无障碍服务相同的水平手势数值校准 Gallery；不查看图片内容。
    if len(items) < 2:
        result={'left':False,'reason':'single_page','target':items[0]['source']}
        save('flip-calibration.json',result)
        open_page(items[0])
        return result
    matcher=Matcher(items)
    size=adb('shell','wm','size').decode(errors='replace')
    match=re.search(r'(\d+)x(\d+)',size)
    width,height=map(int,match.groups()) if match else (1080,1920)
    target=items[1]['source']; attempts=[]
    for left in (False,True):
        open_page(items[0])
        start_x=int(width*(.2 if left else .8));end_x=int(width*(.8 if left else .2))
        adb('shell','input','swipe',start_x,int(height*.55),end_x,int(height*.55),400)
        time.sleep(2)
        image=OUT/('flip-calibration-left.png' if left else 'flip-calibration-right.png')
        image.write_bytes(adb('exec-out','screencap','-p'))
        observed=matcher.match(image)
        attempts.append({'left':left,'start_x':start_x,'end_x':end_x,'match':observed})
    candidates=[attempt['left'] for attempt in attempts if attempt['match']['accepted'] and attempt['match']['source']==target]
    if len(candidates)!=1:
        save('flip-calibration.json',{'target':target,'attempts':attempts,'status':'failed'})
        raise RuntimeError('无法唯一校准 Gallery 下一页手势方向。')
    result={'target':target,'left':candidates[0],'attempts':attempts,'status':'passed'}
    save('flip-calibration.json',result)
    open_page(items[0])
    print('flip calibrated','left' if result['left'] else 'right',flush=True)
    return result


class Matcher:
    def __init__(self,items):
        import cv2
        import numpy as np
        self.cv=cv2; self.np=np
        self.orb=cv2.ORB_create(nfeatures=1600)
        self.bf=cv2.BFMatcher(cv2.NORM_HAMMING)
        self.sources=[]
        for item in items:
            im=self.read(ROOT/item['relative_path'])
            self.sources.append((item['source'],self.orb.detectAndCompute(im,None)[1]))
    def read(self,p):
        im=self.cv.imdecode(self.np.frombuffer(p.read_bytes(),dtype=self.np.uint8),self.cv.IMREAD_GRAYSCALE)
        assert im is not None
        return self.cv.resize(im,(int(im.shape[1]*min(1,1100/max(im.shape))),int(im.shape[0]*min(1,1100/max(im.shape)))))
    def match(self,p):
        d=self.orb.detectAndCompute(self.read(p),None)[1]
        scores=[]
        for name,ref in self.sources:
            good=0 if d is None or ref is None else sum(len(pair)==2 and pair[0].distance<.7*pair[1].distance for pair in self.bf.knnMatch(d,ref,k=2))
            scores.append((good,name))
        scores.sort(reverse=True)
        best=scores[0];runner_up=scores[1][0] if len(scores)>1 else 0
        return {'source':best[1], 'good_matches':best[0], 'runner_up_matches':runner_up, 'accepted':best[0]>=20 and best[0]>runner_up*2}


def collect():
    if (OUT/'run.json').exists():
        raise RuntimeError('Evidence exists; select a new OUT directory before another run.')
    items=json.loads((OUT/'sources.json').read_text(encoding='utf-8'))
    matcher=Matcher(items)
    assert b'org.local.reader' in adb('shell','settings','get','secure','enabled_accessibility_services')
    before={p.stem for p in RAW.glob('*.json')}
    with urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=10) as response:
        health=json.load(response)
    started=time.time(); records=[]; covered=set(); interventions=[]
    # 保留旧 logcat，不清理；按设备日志 epoch 划定本轮边界。logcat 是环形
    # 缓冲区，运行中旧行被淘汰后使用初始行数切片会误删本轮最早事件。
    (OUT/'overlay-before.png').write_bytes(adb('exec-out','screencap','-p'))
    tap('读')
    last_progress=time.time(); last_recovery=0; terminals=0; expected=items[0]['source']
    while time.time()-started<14400:
        logall=adb('logcat','-d','-v','epoch','-s','Reader:I','*:S').decode('utf-8',errors='replace')
        lines=[]
        for line in logall.splitlines():
            try:
                if float(line.split()[0]) >= started:
                    lines.append(line)
            except (ValueError, IndexError):
                continue
        log='\n'.join(lines)+'\n'
        (OUT/'android.log').write_text(log,encoding='utf-8')
        for p in sorted(RAW.glob('*.json'),key=lambda p:p.stat().st_mtime):
            if p.stem in before: continue
            try: data=json.loads(p.read_text(encoding='utf-8'))
            except (ValueError,OSError): continue
            screen=RAW/(p.stem+'.jpg')
            if not screen.exists(): continue
            before.add(p.stem)
            match=matcher.match(screen)
            folder=OUT/'pages'/p.stem; folder.mkdir(parents=True,exist_ok=True)
            shutil.copy2(screen,folder/'screen.jpg'); shutil.copy2(p,folder/'ocr.json')
            source=match['source'] if match['accepted'] else None
            records.append({'page_id':p.stem,'source':source,'match':match,'expected_after_recovery':expected,'observed_at':time.time(),'units':len(data),'segments':sum(len(x.get('segments',[])) for x in data),'characters':sum(len(x['text']) for x in data)})
            if source: covered.add(source)
            last_progress=time.time()
            print('captured',len(covered),'/',len(items),'file',source,'units',len(data),flush=True)
        for rec in records:
            raw_page = RAW / rec['page_id']
            units_dir = OUT / 'pages' / rec['page_id'] / 'units'
            units_dir.mkdir(parents=True, exist_ok=True)
            previous_count = len(rec.get('audio_units', []))
            audio_units = []
            for unit_index in range(1, int(rec.get('units', 0)) + 1):
                stem = f'{unit_index:03d}'
                src = raw_page / f'{stem}.wav'
                if not src.exists():
                    continue
                dest = units_dir / f'{stem}.wav'
                shutil.copy2(src, dest)
                trace = raw_page / f'{stem}.tts.json'
                trace_dest = units_dir / f'{stem}.tts.json'
                if trace.exists():
                    shutil.copy2(trace, trace_dest)
                with wave.open(str(dest), 'rb') as w:
                    frames = w.getnframes()
                    sample_rate = w.getframerate()
                    audio_units.append({
                        'index': unit_index,
                        'audio': f'units/{stem}.wav',
                        'trace': f'units/{stem}.tts.json' if trace_dest.exists() else None,
                        'seconds': round(frames / sample_rate, 3),
                        'sample_rate': sample_rate,
                        'channels': w.getnchannels(),
                        'sample_width': w.getsampwidth(),
                        'frames': frames,
                        'sha256': digest(dest),
                    })
            rec['audio_units'] = audio_units
            rec['audio_seconds'] = round(sum(unit['seconds'] for unit in audio_units), 3)
            rec['audio_complete'] = len(audio_units) == int(rec.get('units', 0))
            if rec['audio_complete'] and audio_units:
                rec['page_audio'] = concatenate_wavs([units_dir/f"{unit['index']:03d}.wav" for unit in audio_units],
                                                     OUT/'pages'/rec['page_id']/'page.wav')
            if len(audio_units) > previous_count:
                last_progress = time.time()
                print('audio', rec['source'], len(audio_units), '/', rec['units'], rec['audio_seconds'], flush=True)
        # 所有正文只保留在原始本地证据，控制台仅打印计数。
        events=[]
        for line in lines:
            if 'Reader' not in line: continue
            msg=line.split('Reader',1)[1].lstrip(' :')
            typ = 'completed' if '本页完成' in msg else 'recognized' if '整页已识别' in msg else 'upload' if '电脑正在识别' in msg else 'playing' if re.match(r'\d+/\d+ ',msg) else 'page_unchanged' if '画面未变化' in msg else 'no_text' if '未识别到文字' in msg else 'error' if any(s in msg for s in ['失败','无法截图','被取消','请开启']) else None
            if typ: events.append({'epoch':float(line.split()[0]),'type':typ})
        terminal=[e for e in events if e['type'] in ('page_unchanged','no_text','error')]
        state={'speech_policy':health.get('speech_policy'), 'started_epoch':started,'elapsed_seconds':round(time.time()-started,2),'covered':len(covered),'expected_pages':len(items),'records':records,'events':events,'interventions':interventions,'finished':False}
        save('run.json',state)
        if (OUT/'stop-requested.json').exists():
            state['stopped_by_user']=True
            save('run.json',state)
            print('stopped by user; evidence retained',flush=True)
            return
        if len(terminal)>terminals:
            terminals=len(terminal); last_progress=time.time()
            if len(covered)==len(items):
                state['finished']=True; state['terminal']=terminal[-1]; save('run.json',state)
                (OUT/'end.png').write_bytes(adb('exec-out','screencap','-p'))
                print('finished',len(covered),'pages',flush=True);return
            remaining=[p for p in items if p['source'] not in covered]
            nextitem=remaining[0]
            interventions.append({'epoch':time.time(),'reason':terminal[-1]['type'],'resume_source':nextitem['source']})
            tap('停'); open_page(nextitem); tap('读'); expected=nextitem['source']; last_recovery=time.time()
            print('recovery',terminal[-1]['type'],'resume',expected,flush=True)
        if time.time()-last_progress>660:
            interventions.append({'epoch':time.time(),'reason':'watchdog_660_seconds'})
            save('run.json',state)
            print('watchdog stalled; inspect metadata',flush=True);return
        time.sleep(3)
    print('time limit reached',flush=True)


if __name__=='__main__':
    mode=argparse.ArgumentParser(); mode.add_argument('mode',choices=['prepare','collect']); args=mode.parse_args()
    prepare() if args.mode=='prepare' else collect()
