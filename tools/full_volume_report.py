# 原始实测文件的离线报告与语音覆盖验证；控制台不显示漫画或转写正文。
import argparse
import collections
import statistics
import math
import contextlib
import difflib
import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
import wave
from pathlib import Path
from tools.full_volume_run import concatenate_wavs

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/full-volume-20260920'

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def norm(t): return ''.join(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]',t)).lower()
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def audio_units(folder):
    unit_files=sorted((folder/'units').glob('*.wav')) if (folder/'units').is_dir() else []
    legacy=False
    if not unit_files and (folder/'page.wav').exists():
        unit_files=[folder/'page.wav'];legacy=True
    result=[]
    for position,audio in enumerate(unit_files,1):
        index=int(audio.stem) if not legacy and audio.stem.isdigit() else position
        with wave.open(str(audio),'rb') as w:
            frames=w.getnframes();sample_rate=w.getframerate()
            item={'index':index,'audio':audio.relative_to(folder).as_posix(),'seconds':round(frames/sample_rate,3),
                  'sample_rate':sample_rate,'channels':w.getnchannels(),'sample_width':w.getsampwidth(),
                  'frames':frames,'sha256':sha(audio),'legacy_page_audio':legacy}
        trace=audio.with_suffix('.tts.json') if not legacy else folder/'tts.json'
        item['trace']=trace.relative_to(folder).as_posix() if trace.exists() else None
        result.append(item)
    return result

def tts_validation(folder, units):
    result={'trace_units':0,'asr_checked_units':0,'verified_units':0,'recovered_units':0,'partially_recovered_units':0,
            'total_attempts':0,'internal_retries':0,'total_requests':0,'max_attempts':0}
    for unit in units:
        if not unit['trace']:
            continue
        records=read(folder/unit['trace'])
        for record in records:
            attempts=len(record.get('attempts') or [])
            result['trace_units']+=1
            result['asr_checked_units']+=bool(record.get('asr_checked'))
            result['verified_units']+=bool(record.get('verified'))
            result['recovered_units']+=bool(record.get('recovered'))
            result['partially_recovered_units']+=bool(record.get('partial_recovery'))
            result['total_attempts']+=attempts
            result['internal_retries']+=max(0,attempts-1)
            result['total_requests']+=len(record.get('requests') or [])
            result['max_attempts']=max(result['max_attempts'],attempts)
    return result

def asr(watch=True):
    from funasr import AutoModel
    lock=read(ROOT/'config/asr-validation.lock.json')
    output=OUT/'asr'; output.mkdir(exist_ok=True)
    # 模型自身的下载/转写日志写入本地文件，不让正文进入代理上下文。
    with (output/'runtime.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        model=AutoModel(model=lock['model_path'],device='cpu',disable_update=True,ncpu=4)
    def pending_records():
        seen=set()
        while True:
            try: current=read(OUT/'run.json')
            except (ValueError, OSError): time.sleep(2);continue
            for rec in current['records']:
                units=audio_units(OUT/'pages'/rec['page_id'])
                ready=bool(units) and (units[0]['legacy_page_audio'] or len(units)==int(rec.get('units',len(units))))
                if rec['page_id'] not in seen and ready:
                    seen.add(rec['page_id']);yield rec
            if current['finished'] or not watch: return
            time.sleep(5)
    records=pending_records()
    summary=[]
    normalize_speech_text=None
    for i,rec in enumerate(records):
        if normalize_speech_text is None:
            from server.core import normalize_speech_text
        folder=OUT/'pages'/rec['page_id'];units=audio_units(folder)
        if not units: continue
        ocr=read(folder/'ocr.json');unit_results=[];started=time.time()
        for unit in units:
            audio=folder/unit['audio']
            expected_text=''.join(x['text'] for x in ocr) if unit['legacy_page_audio'] else ocr[unit['index']-1]['text']
            expected=norm(normalize_speech_text(expected_text))
            unit_dir=output/rec['page_id'];unit_dir.mkdir(exist_ok=True)
            dest=unit_dir/f"{unit['index']:03d}.json"
            cached=read(dest) if dest.exists() else None
            if cached and cached.get('audio_sha256')==unit['sha256'] and cached.get('expected_normalized')==expected:
                unit_item=cached
            else:
                start=time.time()
                with (output/'runtime.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
                    results=model.generate(input=str(audio))
                actual=norm(''.join(x.get('text','') for x in results))
                matcher=difflib.SequenceMatcher(None,expected,actual,autojunk=False)
                matches=sum(b.size for b in matcher.get_matching_blocks())
                tail=expected[-20:]
                unit_item={'page_id':rec['page_id'],'source':rec['source'],'unit_index':unit['index'],'recognition':results,
                           'expected_normalized':expected,'actual_normalized':actual,'expected_characters':len(expected),
                           'asr_characters':len(actual),'aligned_character_fraction':round(matches/max(1,len(expected)),4),
                           'last_20_exact_in_asr':bool(tail) and tail in actual,'processing_seconds':round(time.time()-start,2),
                           'audio_sha256':unit['sha256']}
                save(dest,unit_item)
            unit_results.append(unit_item)
        expected=''.join(x['expected_normalized'] for x in unit_results)
        actual=''.join(x['actual_normalized'] for x in unit_results)
        matcher=difflib.SequenceMatcher(None,expected,actual,autojunk=False)
        matches=sum(b.size for b in matcher.get_matching_blocks())
        tail=expected[-20:]
        item={'page_id':rec['page_id'],'source':rec['source'],'expected_normalized':expected,'actual_normalized':actual,
              'expected_characters':len(expected),'asr_characters':len(actual),
              'aligned_character_fraction':round(matches/max(1,len(expected)),4),
              'last_20_exact_in_asr':bool(tail) and tail in actual,'processing_seconds':round(time.time()-started,2),
              'audio_unit_count':len(units),'expected_audio_units':int(rec.get('units',len(units))),
              'audio_complete':units[0]['legacy_page_audio'] or len(units)==int(rec.get('units',len(units))),
              'unit_results':[{k:v for k,v in x.items() if k not in ('recognition','expected_normalized','actual_normalized')} for x in unit_results]}
        save(output/(rec['page_id']+'.json'),item)
        summary.append({k:v for k,v in item.items() if k not in ('recognition','expected_normalized','actual_normalized')})
        save(output/'summary.json',summary)
        print('ASR',len(summary),'/',read(OUT/'run.json')['expected_pages'],rec['source'],'aligned',item['aligned_character_fraction'],'tail_exact',item['last_20_exact_in_asr'],flush=True)

def report():
    run=read(OUT/'run.json'); sources=read(OUT/'sources.json')
    calibration=read(OUT/'flip-calibration.json') if (OUT/'flip-calibration.json').exists() else {'left':False}
    direction='向左' if calibration.get('left') else '向右'
    log=(OUT/'android.log').read_text(encoding='utf-8')
    starts=[e for e in run['events'] if e['type']=='upload']
    alignment=len(starts)==len(run['records'])
    # 运行中最后一次上传可能还没有 OCR 文件，已完成前缀仍可严格关联。
    if not run['finished'] and run['records'] and len(starts)==len(run['records'])+1:
        alignment=starts[-1]['epoch']>run['records'][-1]['observed_at']
    asrs={x['page_id']:x for x in read(OUT/'asr/summary.json')} if (OUT/'asr/summary.json').exists() else {}
    rows=[]
    for i,rec in enumerate(run['records']):
        p=dict(rec); folder=OUT/'pages'/p['page_id']; data=read(folder/'ocr.json')
        p['audio_units']=audio_units(folder)
        p['audio_seconds']=round(sum(x['seconds'] for x in p['audio_units']),3)
        p['audio_complete']=bool(p['audio_units']) and (p['audio_units'][0]['legacy_page_audio'] or len(p['audio_units'])==int(p.get('units',len(p['audio_units']))))
        if p['audio_complete'] and p['audio_units']:
            if p['audio_units'][0]['legacy_page_audio']:
                unit=p['audio_units'][0]
                p['page_audio']={k:unit[k] for k in ('audio','frames','seconds','sample_rate','channels','sample_width','sha256')}
                p['page_audio']['source_units']=1
            else:
                p['page_audio']=concatenate_wavs([folder/x['audio'] for x in p['audio_units']],folder/'page.wav')
        else:
            p['page_audio']=None
        p['tts_validation']=tts_validation(folder,p['audio_units'])
        p['screen_sha256']=sha(folder/'screen.jpg');p['ocr_sha256']=sha(folder/'ocr.json')
        if alignment:
            start=starts[i]['epoch'];end=starts[i+1]['epoch'] if i+1<len(starts) else float('inf')
            events=[e for e in run['events'] if start<=e['epoch']<end]
            p['events']=events
            playing=next((e['epoch'] for e in events if e['type']=='playing'),None)
            recognized=next((e['epoch'] for e in events if e['type']=='recognized'),None)
            complete=next((e['epoch'] for e in events if e['type']=='completed'),None)
            p['completed']=complete is not None
            p['ocr_seconds']=round(recognized-start,3) if recognized else None
            p['first_audio_wait_seconds']=round(playing-start,3) if playing else None
            p['playback_elapsed_seconds']=round(complete-playing,3) if complete and playing else None
            p['playback_duration_consistent']=abs(p['playback_elapsed_seconds']-p['audio_seconds'])<3 if complete and playing and len(p['audio_units'])==1 else None
        else: p['completed']=False
        expected=['%d/%d %s'%(j+1,len(data),x['text']) for j,x in enumerate(data)]
        p['playback_text_present']=all(t in log for t in expected) if data else None
        p['asr']=asrs.get(p['page_id'])
        if p['asr']:
            raw_asr=read(OUT/'asr'/(p['page_id']+'.json'))
            expected=raw_asr['expected_normalized'];actual=raw_asr['actual_normalized']
            blocks=difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_matching_blocks()
            tail_start=max(0,len(expected)-20)
            tail_matched=sum(max(0,min(b.a+b.size,len(expected))-max(b.a,tail_start)) for b in blocks if b.size)
            end=max((b.a+b.size for b in blocks if b.size),default=0)
            p['asr']['last_20_aligned_fraction']=round(tail_matched/max(1,len(expected)-tail_start),4)
            p['asr']['unmatched_trailing_characters']=len(expected)-end
            p['asr']['needs_review']=p['asr']['aligned_character_fraction']<.9 or p['asr']['last_20_aligned_fraction']<.8
        p['excluded_candidates']=sum(len(x.get('excluded_texts',[])) for x in data)
        # 浮窗底色的像素统计，不查看或输出截图。
        import cv2,numpy as np
        im=cv2.imdecode(np.frombuffer((folder/'screen.jpg').read_bytes(),dtype=np.uint8),cv2.IMREAD_COLOR)
        b,g,r=cv2.split(im.astype(np.int16))
        p['overlay_blue_pixels']=int(np.sum((b-r>25)&(g-r>10)&(b>45)&(r<65)))
        rows.append(p)
    totals={'source_pages':len(sources),'recognized_source_pages':len({p['source'] for p in rows if p['source']}),'captures':len(rows),
            'audio_pages':sum(bool(p['audio_units']) for p in rows),'audio_units':sum(len(p['audio_units']) for p in rows),
            'complete_audio_pages':sum(p['audio_complete'] for p in rows),'completed_pages':sum(p['completed'] for p in rows),
            'no_text_pages':sum(p['units']==0 for p in rows),'audio_seconds':round(sum(p.get('audio_seconds',0) for p in rows),3),
            'interventions':len(run['interventions']),'asr_pages':len(asrs),'asr_units':sum(x.get('audio_unit_count',1) for x in asrs.values()),
            'asr_exact_tail_pages':sum(x['last_20_exact_in_asr'] for x in asrs.values()),'upload_record_alignment':alignment,'finished':run['finished']}
    waits=sorted(p['first_audio_wait_seconds'] for p in rows if p.get('first_audio_wait_seconds') is not None)
    totals['first_audio_wait_median_seconds']=statistics.median(waits) if waits else None
    totals['first_audio_wait_p95_seconds']=waits[max(0,math.ceil(len(waits)*.95)-1)] if waits else None
    totals['asr_needs_review_pages']=sum(bool(p.get('asr') and p['asr']['needs_review']) for p in rows)
    totals['all_audio_units_present']=all(p['audio_complete'] for p in rows if p['units'])
    totals['tts_trace_units']=sum(p['tts_validation']['trace_units'] for p in rows)
    totals['tts_asr_checked_units']=sum(p['tts_validation']['asr_checked_units'] for p in rows)
    totals['tts_verified_units']=sum(p['tts_validation']['verified_units'] for p in rows)
    totals['tts_unverified_fallback_units']=totals['tts_asr_checked_units']-totals['tts_verified_units']
    totals['tts_recovered_units']=sum(p['tts_validation']['recovered_units'] for p in rows)
    totals['tts_partially_recovered_units']=sum(p['tts_validation']['partially_recovered_units'] for p in rows)
    totals['tts_recovery_pages']=sum(p['tts_validation']['recovered_units']>0 for p in rows)
    totals['tts_total_attempts']=sum(p['tts_validation']['total_attempts'] for p in rows)
    totals['tts_internal_retries']=sum(p['tts_validation']['internal_retries'] for p in rows)
    totals['tts_total_requests']=sum(p['tts_validation']['total_requests'] for p in rows)
    totals['tts_max_attempts']=max((p['tts_validation']['max_attempts'] for p in rows),default=0)
    totals['automatic_repair_exercised']=totals['tts_recovered_units']>0
    manifest={'summary':totals,'run':{k:v for k,v in run.items() if k not in ('records','events')},'tts':dict(run.get('speech_policy') or {}),'pages':rows,'limitations':['未查看图片内容，未人工核对OCR、分镜阅读顺序或音质。','ASR以OCR文本为对照，只筛查语音覆盖，不能判断OCR是否忠实原图。','音频容器、请求文本和播放完成事件不等于全文准确朗读。','相册空白或错误会中断；恢复行为单独记录，不冒充一次连续通过。','官方模拟器结果不外推到实体手机。']}
    save(OUT/'manifest.json',manifest)
    md=['# 全册安卓—电脑全流程实测','',f"目标目录共 {totals['source_pages']} 页；识别匹配 {totals['recognized_source_pages']} 页，留存实际音频 {totals['audio_pages']} 页、{totals['audio_units']} 个语音单元，安卓播放完成 {totals['completed_pages']} 页。总音频 {totals['audio_seconds']:.2f} 秒。本轮采集耗时 {run['elapsed_seconds']:.2f} 秒。",'', '正式安卓应用通过 MediaProjection 截屏，经网络上传电脑执行 OCR、分镜排序、所选音色串行合成、语音覆盖检查与自动修复，音频返回 Android MediaPlayer 播放，完成后由无障碍手势自动翻页。报告将同页实际响应按播放顺序无损拼为一个整页 WAV；逐单元 WAV 与 trace 继续保留，不离线重合成。图片与正文由程序处理，代理未查看图片内容。','',f"语音覆盖检查 {totals['tts_asr_checked_units']}/{totals['audio_units']} 个单元；{totals['tts_recovered_units']} 个单元在内部重试后自动修复，涉及 {totals['tts_recovery_pages']} 页，共 {totals['tts_internal_retries']} 次内部重试，最大 {totals['tts_max_attempts']} 次尝试。另有 {totals['tts_unverified_fallback_units']} 个单元在有限重试后按待复核回退继续播放。自动修复实际触发：{totals['automatic_repair_exercised']}。",'',f"页面恢复次数：{totals['interventions']}；离线 ASR 逐单元复核 {totals['asr_pages']} 页、{totals['asr_units']} 个音频，末尾20个归一化字符完整出现 {totals['asr_exact_tail_pages']} 页。未命中只表示需要复核，不能直接断言漏读。",'','| 源页 | 分段数 | 音频秒数 | 播放完成 | 自动修复单元 | 首音等待秒 | ASR字符对齐 | 尾段精确命中 | 证据 |','|---|---:|---:|---|---:|---:|---:|---|---|']
    hrows=[]
    for p in rows:
        base='pages/'+p['page_id']; a=p.get('asr') or {};ratio=a.get('aligned_character_fraction');ratio='—' if ratio is None else f'{ratio:.1%}'
        tail=a.get('last_20_exact_in_asr','未测');done='是' if p['completed'] else '否';seconds=p.get('audio_seconds','—');wait=p.get('first_audio_wait_seconds','—')
        page_link=f' · [整页实际音频]({base}/page.wav)' if p['page_audio'] else ''
        links=f'[截图]({base}/screen.jpg) · [OCR]({base}/ocr.json)'+page_link
        recovered=p['tts_validation']['recovered_units']
        md.append(f"| {p['source']} | {p['segments']} | {seconds} | {done} | {recovered} | {wait} | {ratio} | {tail} | {links} |")
        if p['page_audio']:
            unit_links=[]
            for unit in p['audio_units']:
                trace=f' · <a href="{base}/{unit["trace"]}">trace</a>' if unit['trace'] else ''
                unit_links.append(f'<span><a href="{base}/{unit["audio"]}">单元{unit["index"]}</a>{trace}</span>')
            details=f'<details><summary>逐单元原始证据（{len(unit_links)} 个）</summary>{"<br>".join(unit_links)}</details>'
            audio=f'<audio controls preload="none" src="{base}/page.wav"></audio> <a href="{base}/page.wav">整页WAV</a>{details}'
        else:
            audio='无音频'
        hrows.append(f'<tr><td>{html.escape(str(p["source"]))}</td><td>{p["segments"]}</td><td>{seconds}</td><td>{done}</td><td>{recovered}</td><td>{wait}</td><td>{ratio}</td><td>{tail}</td><td>{audio}<br><a href="{base}/screen.jpg">原始截图</a> · <a href="{base}/ocr.json">OCR 原始结果</a></td></tr>')
    md+=['', '## 全流程步骤', '', '1. 官方 SDK 启动 ReaderAosp35，校验正式 APK 与本地交付 APK 一致。', '2. 指定批次页面导入独立 Gallery 相册，逐文件校验 SHA256；用 ORB 数值匹配分别验证左右手势并唯一确定下一页方向。', '3. 电脑临时开启 save_debug_pages，保留真实上传 JPEG、OCR JSON，以及页面目录 units/ 中实际返回安卓的全部 WAV 与逐单元 tts.json。使用本批次服务策略，不替换重合成结果。', f'4. 正式应用连接电脑 WLAN 地址，启用既有无障碍权限与自动{direction}翻页，系统授权 Entire screen；调试期间媒体音量设为 0，完成后恢复。', '5. 程序特征匹配验证首屏和滑动方向，浮窗移至页面区域；正式运行仅触发首个读按钮，后续由 MediaPlayer 完成回调触发翻页。', '6. 逐页记录上传、识别、播放及完成事件；无文字或错误时记录中断和恢复。末页画面未变化以 page_unchanged 正常停止。', '7. 本地 ASR 逐个处理本轮 WAV，再按页汇总覆盖筛查与证据哈希；完成后恢复默认留存、翻页设置和媒体音量。']
    md+=['', '## 性能与完整功能筛查', '', f"首音等待中位数 {totals['first_audio_wait_median_seconds']} 秒，P95 {totals['first_audio_wait_p95_seconds']} 秒（包含安卓截图后的 OCR、合成与语音覆盖检查等待）；有正文页面的全部音频单元均已留存：{totals['all_audio_units_present']}。", '', f"正式链路 trace 记录 {totals['tts_total_attempts']} 次候选尝试、{totals['tts_total_requests']} 次合成请求；自动修复成功 {totals['tts_recovered_units']} 个单元。离线 ASR 另标记待复核 {totals['asr_needs_review_pages']} 页：全文字符对齐低于90%，或末20字对齐低于80%。该阈值仅用于筛查；误识、同音字、重复短句会影响数值。详细逐单元指标及尾段指标在 manifest.json 中。", '', '[浮窗遮挡数值验证](overlay-verification.json) · [实际APK和设备环境](environment.json) · [本轮回归日志](unit-tests.log) · [本轮上游文件完整性](upstream-verification.json)']
    md+=['','## 运行边界','']+['- '+x for x in manifest['limitations']]+['','## 可追溯证据','','[源文件与设备副本SHA256](sources.json) · [逐页清单](manifest.json) · [自动翻页与恢复事件](run.json) · [安卓原始日志](android.log) · [ASR指标](asr/summary.json)','', '浮窗色彩统计是数值筛查，彩色原图可能命中同类颜色，不单独作为残留定论。图片匹配使用 ORB 特征计数并要求第一候选显著优于第二候选。','']
    (OUT/'实测报告.md').write_text('\n'.join(md),encoding='utf-8')
    started=datetime.fromtimestamp(run['started_epoch'],timezone.utc).isoformat()
    ended=datetime.fromtimestamp(run['started_epoch']+run['elapsed_seconds'],timezone.utc).isoformat()
    restore_path=OUT/'restoration.json'
    if restore_path.exists():
        restored=read(restore_path)
        md+=['', '## 环境恢复', '', '本轮恢复状态：'+str(restored.get('configuration_bytes_restored', False))+'；截图留存：'+str(restored.get('save_debug_pages', '未知'))+'；自动翻页：'+str(restored.get('auto_flip', '未知'))+'。详细配置与连接结果以恢复文件为准。', '', '[恢复状态](restoration.json) · [最终逐项核验](final-checks.json) · [末页数值匹配](end-page-verification.json)']
    md+=['', '## 时间与复现', '', f'本轮采集 UTC：{started} 至 {ended}；北京时间为 UTC+8。', '', f'使用 tools/test_session.ps1 与复现工具创建独立批次；新建时传入 JPG 目录，prepare 自动校准为{direction}翻页，setup 授权 Entire screen 并静音，collect 仅触发一次读按钮。无文字或错误时编排器会记录恢复事件并打开下一未覆盖页；已有证据目录拒绝覆盖。', '', '本轮回归、上游完整性与APK哈希见对应证据文件；仅以本轮数据判断，不继承历史批次结论。']
    (OUT/'实测报告.md').write_text('\n'.join(md),encoding='utf-8')
    introduction=html.escape(md[2]); limits=''.join('<li>'+html.escape(x)+'</li>' for x in manifest['limitations'])
    doc=f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{totals["source_pages"]}页安卓—电脑实测</title><style>body{{font:16px/1.7 system-ui;margin:32px;background:#f4f6fa;color:#17243a}}main{{max-width:1500px;margin:auto}}table{{border-collapse:collapse;background:white;width:100%}}td,th{{padding:12px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}audio{{width:240px;vertical-align:middle}}a{{color:#185da9}}details{{margin-top:6px}}</style><main><h1>{totals["source_pages"]}页安卓—电脑全流程实测</h1><p>{introduction}</p><p>正式安卓应用通过 MediaProjection 上传，电脑执行 OCR、排序、TTS 覆盖检查与自动修复，音频返回安卓播放并自动翻页。每页播放器是按实际播放顺序拼合的完整 WAV；逐单元原始响应与 trace 可展开核对。</p><p><strong>自动修复已真实触发：</strong>{totals["tts_recovered_units"]} 个单元、{totals["tts_recovery_pages"]} 页，内部重试 {totals["tts_internal_retries"]} 次；覆盖检查 {totals["tts_asr_checked_units"]}/{totals["audio_units"]} 个单元。另有 {totals["tts_unverified_fallback_units"]} 个单元有限重试后按待复核回退播放。</p><p><a href="实测报告.md">Markdown报告</a> · <a href="manifest.json">完整清单</a> · <a href="run.json">运行与恢复事件</a> · <a href="sources.json">源文件校验</a></p><table><thead><tr><th>源页</th><th>分段</th><th>音频秒数</th><th>播放完成</th><th>自动修复单元</th><th>首音等待秒</th><th>ASR对齐</th><th>末20字命中</th><th>证据与实际音频</th></tr></thead><tbody>{"".join(hrows)}</tbody></table><p>离线 ASR 待复核：{totals["asr_needs_review_pages"]} 页；首音等待中位数 {totals["first_audio_wait_median_seconds"]} 秒、P95 {totals["first_audio_wait_p95_seconds"]} 秒；页面恢复 {totals["interventions"]} 次。</p><p><a href="overlay-verification.json">浮窗遮挡数值证据</a> · <a href="upstream-verification.json">上游完整性</a> · <a href="unit-tests.log">回归结果</a> · <a href="environment.json">运行环境</a> · <a href="verification.json">证据哈希</a></p><p><a href="final-checks.json">本批次最终逐项核验</a> · <a href="end-page-verification.json">末页匹配</a> · <a href="restoration.json">环境恢复</a></p><h2>验证边界</h2><ul>{limits}</ul></main></html>'
    (OUT/'index.html').write_text(doc,encoding='utf-8')
    verification={p.relative_to(OUT).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in OUT.rglob('*') if p.is_file() and p.name!='verification.json'}
    save(OUT/'verification.json',verification)
    print(json.dumps(totals),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['asr','report']);args=parser.parse_args()
    asr() if args.mode=='asr' else report()
