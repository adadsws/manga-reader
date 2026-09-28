# 用控件树及正式服务 dumpsys 验证浮窗开关；不截屏，不打开漫画。
import argparse
import json
import re
import time
from pathlib import Path
from tools.android_ui import adb, nodes, tap
from tools.full_volume_run import tap as scroll_tap

ROOT = Path(__file__).resolve().parents[1]
LABEL = '读完一页后自动翻页'

def settings():
    adb('shell', 'am', 'start', '-W', '-n', 'org.local.reader/.MainActivity')
    time.sleep(.5)

def checked():
    return next(n for n in nodes() if n.get('text') == LABEL).get('checked') == 'true'

def start_overlay():
    scroll_tap('3  保存并启动朗读浮窗')
    time.sleep(.6)
    labels = {n.get('text') for n in nodes()}
    if 'A single app' in labels:
        tap('A single app'); tap('Entire screen')
    elif '单个应用' in labels:
        tap('单个应用'); tap('整个屏幕')
    labels = {n.get('text') for n in nodes()}
    tap(next(x for x in ('START', '立即开始', '开始') if x in labels))
    time.sleep(1)
    adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
    time.sleep(.5)

def state():
    for _ in range(12):
        data = adb('shell', 'dumpsys', 'activity', 'service', 'org.local.reader/.ReaderService').replace('\r', '')
        match = re.search(r'autoFlip=(true|false) asrCheck=(?:true|false) paused=(true|false) running=(true|false) pauseEnabled=(true|false) resumeCapture=(true|false) autoBounds=([0-9]+),([0-9]+),([0-9]+),([0-9]+)', data)
        if match:
            result={'auto':match[1]=='true','paused':match[2]=='true','running':match[3]=='true','pause_enabled':match[4]=='true','bounds':list(map(int,match.groups()[5:]))}
            controls=re.search(r'controls=([^@;]+)@(\d+),(\d+),(\d+),(\d+);([^@;]+)@(\d+),(\d+),(\d+),(\d+);([^@;]+)@(\d+),(\d+),(\d+),(\d+)',data)
            if controls:
                values=controls.groups();result['controls']=[]
                for offset in (0,5,10):
                    result['controls'].append({'label':values[offset],'bounds':list(map(int,values[offset+1:offset+5]))})
            return result
        time.sleep(.25)
    raise RuntimeError('浮窗状态不可读取：' + data.strip()[-300:])

def toggle():
    x,y,r,b = state()['bounds']
    assert r>x and b>y
    adb('shell','input','tap',str((x+r)//2),str((y+b)//2))
    time.sleep(.3)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--out',default='~outputs-intermediate/evidence/overlay-auto-20260920'); args=parser.parse_args()
    assert adb('emu','avd','name').splitlines()[0]=='ReaderAosp35'
    settings(); original=checked(); results={'original_auto':original, 'images_viewed':False}
    try:
        start_overlay(); initial=state(); assert initial['auto']==original
        controls=initial['controls'];widths=[x['bounds'][2]-x['bounds'][0] for x in controls]
        assert [x['label'] for x in controls]==['重新开始','暂停','关闭浮窗']
        assert not initial['running'] and not initial['pause_enabled'] and abs(widths[0]-widths[1])<=2 and widths[2]<widths[0]*.7
        results['controls']={'labels':[x['label'] for x in controls],'widths':widths,'pause_disabled_while_idle':True}
        toggle(); assert state()['auto']!=original
        settings(); assert checked()!=original
        results['overlay_to_settings']=True
        tap(LABEL); assert state()['auto']==original
        results['settings_to_overlay']=True
        toggle(); assert state()['auto']!=original
        settings(); scroll_tap('关闭朗读服务和浮窗'); time.sleep(.5)
        start_overlay(); assert state()['auto']!=original
        results['service_restart_remembers']=True
        results['actual_auto_button_bounds']=state()['bounds']
        toggle(); assert state()['auto']==original
    finally:
        settings()
        if checked()!=original: tap(LABEL)
        scroll_tap('关闭朗读服务和浮窗')
    results['restored_auto']=checked()
    out=ROOT / args.out
    out.mkdir(parents=True,exist_ok=True)
    (out/'ui-checks.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False))
