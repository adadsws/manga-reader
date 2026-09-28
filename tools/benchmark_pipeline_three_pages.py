# 三页正式 HTTP 流程计时：只读取图片字节，不展示或保存 OCR/TTS 正文。
import argparse
import hashlib
import html
import io
import json
import math
import statistics
import time
import urllib.request
import wave
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PAGES=[ROOT/'secrets/manga/0009.jpg',ROOT/'secrets/manga/0012.jpg',ROOT/'secrets/manga/0041.jpg']
OUT=ROOT/'~outputs-intermediate/evidence/pipeline-timing-three-pages-20260921'
BASE='http://127.0.0.1:8765'
CFG=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'))
AUTH={'X-Reader-Token':CFG['token']}
DEBUG=ROOT/'~temp/logs/debug.jsonl'

def call(path,method='GET',body=None,headers=None,timeout=360):
    request=urllib.request.Request(BASE+path,data=body,headers={**AUTH,**(headers or {})},method=method)
    started=time.perf_counter()
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return response.status,response.read(),time.perf_counter()-started

def event_reader():
    offset=DEBUG.stat().st_size if DEBUG.exists() else 0
    def take():
        nonlocal offset
        if not DEBUG.exists():return []
        with DEBUG.open('rb') as stream:
            stream.seek(offset);raw=stream.read();offset=stream.tell()
        output=[]
        for line in raw.splitlines():
            try:
                value=json.loads(line)
                if isinstance(value,dict):output.append(value)
            except (ValueError,UnicodeDecodeError):
                pass
        return output
    return take

def span(events,start_name,end_name):
    start=next((x for x in events if x.get('event')==start_name),None)
    end=next((x for x in events if x.get('event')==end_name),None)
    if not start or not end:return 0.0
    return max(0.0,(datetime.fromisoformat(end['time'])-datetime.fromisoformat(start['time'])).total_seconds())

def percentile(values,fraction):
    values=sorted(values)
    position=(len(values)-1)*fraction
    lower=math.floor(position);upper=math.ceil(position)
    if lower==upper:return values[lower]
    return values[lower]+(values[upper]-values[lower])*(position-lower)

def wav_info(body):
    with wave.open(io.BytesIO(body),'rb') as stream:
        frames=stream.getnframes();rate=stream.getframerate()
        pcm=stream.readframes(frames)
        expected=frames*stream.getnchannels()*stream.getsampwidth()
        if not frames or len(pcm)!=expected:raise ValueError('WAV 不完整')
        return round(frames/rate,3)

def main():
    global OUT
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default=str(OUT))
    parser.add_argument('--all-warm',action='store_true')
    args=parser.parse_args()
    OUT=Path(args.out)
    if not OUT.is_absolute():OUT=ROOT/OUT
    if OUT.exists():raise SystemExit('证据目录已存在，拒绝覆盖：'+str(OUT))
    if not all(p.is_file() for p in PAGES):raise SystemExit('三页输入不完整')
    OUT.mkdir(parents=True)
    take_events=event_reader()
    status,raw,health_seconds=call('/health',timeout=15)
    health=json.loads(raw)
    take_events()
    pages=[];units=[];all_events=[]
    wall_started=time.perf_counter()
    for source in PAGES:
        body=source.read_bytes()
        status,raw,ocr_http=call('/pages','POST',body,{'Content-Type':'image/jpeg'})
        ocr_events=take_events();all_events.extend(ocr_events)
        if status!=200:raise RuntimeError('OCR HTTP '+str(status))
        payload=json.loads(raw);page_id=payload['page_id'];sentences=payload['sentences']
        ocr_total=next((x.get('seconds',0.0) for x in reversed(ocr_events) if x.get('event')=='ocr_end'),0.0)
        ocr_load=span(ocr_events,'ocr_model_loading','ocr_model_ready')
        ocr_infer=span(ocr_events,'ocr_start','ocr_end')
        page_units=[]
        try:
            for index in range(len(sentences)):
                status,audio,audio_http=call(
                    f'/pages/{page_id}/audio/{index}',headers={'X-Reader-ASR-Check':'all'})
                audio_events=take_events();all_events.extend(audio_events)
                if status!=200:raise RuntimeError(f'音频 HTTP {status}')
                duration=wav_info(audio)
                trace_path=ROOT/'~outputs-intermediate/pages'/page_id/f'{index+1:03}.tts.json'
                trace=json.loads(trace_path.read_text(encoding='utf-8'))
                attempts=[attempt for chunk in trace for attempt in chunk.get('attempts',[])]
                synthesis=round(sum(float(x.get('synthesis_seconds',0)) for x in attempts),3)
                asr=round(sum(float(x.get('asr_seconds',0)) for x in attempts),3)
                other=round(max(0.0,audio_http-synthesis-asr),3)
                recovered=any(bool(x.get('recovered')) for x in trace)
                unverified=any(x.get('verified') is False for x in trace)
                row={
                    'page':source.stem,'unit':index+1,'http_status':status,
                    'http_seconds':round(audio_http,3),'synthesis_seconds':synthesis,
                    'asr_seconds':asr,'other_seconds':other,'attempts':len(attempts),
                    'recovered':recovered,'unverified':unverified,
                    'wav_bytes':len(audio),'wav_seconds':duration,
                    'wav_sha256':hashlib.sha256(audio).hexdigest(),
                }
                units.append(row);page_units.append(row)
                print('AUDIO',source.stem,index+1,'/',len(sentences),row['http_seconds'],flush=True)
        finally:
            try:call('/pages/'+page_id,'DELETE',timeout=15)
            finally:
                all_events.extend(take_events())
        pages.append({
            'page':source.stem,'source_bytes':len(body),'units':len(page_units),
            'ocr_http_seconds':round(ocr_http,3),'ocr_server_seconds':round(float(ocr_total),3),
            'ocr_model_load_seconds':round(ocr_load,3),'ocr_inference_seconds':round(ocr_infer,3),
            'upload_response_overhead_seconds':round(max(0.0,ocr_http-float(ocr_total)),3),
            'tts_synthesis_seconds':round(sum(x['synthesis_seconds'] for x in page_units),3),
            'asr_seconds':round(sum(x['asr_seconds'] for x in page_units),3),
            'audio_http_seconds':round(sum(x['http_seconds'] for x in page_units),3),
            'first_audio_ready_seconds':round(ocr_http+(page_units[0]['http_seconds'] if page_units else 0),3),
            'page_processing_seconds':round(ocr_http+sum(x['http_seconds'] for x in page_units),3),
            'attempts':sum(x['attempts'] for x in page_units),
            'recovered_units':sum(x['recovered'] for x in page_units),
            'unverified_units':sum(x['unverified'] for x in page_units),
        })
        print('PAGE',source.stem,pages[-1]['page_processing_seconds'],flush=True)
    total_wall=time.perf_counter()-wall_started
    asr_model_load=span(all_events,'tts_coverage_model_loading','tts_coverage_model_ready')
    summary={
        'environment':{
            'pages':[p.stem for p in PAGES],'reader_cold_start':not args.all_warm,
            'ocr_model_initially_cold':not args.all_warm,'asr_model_initially_cold':not args.all_warm,
            'tts_process_initially_warm':args.all_warm,'startup_warmup_completed':args.all_warm,
            'android_tested':False,
            'images_viewed':False,'voice':health.get('voice_name'),
            'model_version':health.get('model_version'),'speech_policy':health.get('speech_policy'),
        },
        'totals':{
            'pages':len(pages),'units':len(units),'wall_seconds':round(total_wall,3),
            'ocr_http_seconds':round(sum(x['ocr_http_seconds'] for x in pages),3),
            'ocr_model_load_seconds':round(sum(x['ocr_model_load_seconds'] for x in pages),3),
            'ocr_inference_seconds':round(sum(x['ocr_inference_seconds'] for x in pages),3),
            'tts_synthesis_seconds':round(sum(x['synthesis_seconds'] for x in units),3),
            'asr_seconds':round(sum(x['asr_seconds'] for x in units),3),
            'asr_model_load_seconds':round(asr_model_load,3),
            'audio_http_seconds':round(sum(x['http_seconds'] for x in units),3),
            'attempts':sum(x['attempts'] for x in units),
            'recovered_units':sum(x['recovered'] for x in units),
            'unverified_units':sum(x['unverified'] for x in units),
            'all_http_200':all(x['http_status']==200 for x in units),
            'unit_http_median_seconds':round(statistics.median(x['http_seconds'] for x in units),3),
            'unit_http_p95_seconds':round(percentile([x['http_seconds'] for x in units],.95),3),
        },
        'pages':pages,
    }
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'units.json').write_text(json.dumps(units,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    safe_events=[{k:v for k,v in event.items() if k in {'time','pid','event','bytes','rows','chunks','chunk','characters','seconds','status','index'}} for event in all_events]
    (OUT/'events.json').write_text(json.dumps(safe_events,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    page_rows=''.join(
        f"<tr><th>{x['page']}</th><td>{x['units']}</td><td>{x['ocr_http_seconds']:.3f}s"
        + (f"<br>首次模型 {x['ocr_model_load_seconds']:.3f}s" if x['ocr_model_load_seconds'] else '')
        + f"</td><td>{x['tts_synthesis_seconds']:.3f}s</td><td>{x['asr_seconds']:.3f}s</td>"
        f"<td>{x['first_audio_ready_seconds']:.3f}s</td><td>{x['page_processing_seconds']:.3f}s</td>"
        f"<td>{x['attempts']}次 / 恢复{x['recovered_units']} / 待复核{x['unverified_units']}</td></tr>"
        for x in pages)
    unit_rows=''.join(
        f"<tr><td>{x['page']}-{x['unit']}</td><td>{x['synthesis_seconds']:.3f}s</td>"
        f"<td>{x['asr_seconds']:.3f}s</td><td>{x['other_seconds']:.3f}s</td>"
        f"<td>{x['http_seconds']:.3f}s</td><td>{x['attempts']}</td>"
        f"<td>{'恢复' if x['recovered'] else ('待复核' if x['unverified'] else '通过')}</td></tr>"
        for x in units)
    totals=summary['totals'];env=summary['environment']
    doc=f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>三页全流程分步骤耗时</title><style>body{{font:16px/1.7 system-ui;max-width:1200px;margin:30px auto;padding:0 20px}}table{{border-collapse:collapse;width:100%;margin:18px 0}}th,td{{border:1px solid #ddd;padding:10px;text-align:left}}small{{color:#555}}</style><h1>三页全流程分步骤耗时</h1><p>0009、0012、0041 通过正式 <code>/pages</code> 与逐单元 <code>/audio</code> 接口。{'启动自动预热已完成，OCR、TTS、ASR 均为热状态' if args.all_warm else 'Reader、OCR、ASR 从冷状态开始，GPT-SoVITS 为热状态'}；当前模型为 {html.escape(str(env['voice']))} {html.escape(str(env['model_version']))}。未查看图片内容，本轮不含安卓截图、下载和播放。</p><table><tr><th>页</th><th>单元</th><th>上传+OCR</th><th>TTS合成</th><th>ASR检查</th><th>首段可用</th><th>整页处理</th><th>检查结果</th></tr>{page_rows}</table><p><b>三页合计：</b>墙钟 {totals['wall_seconds']:.3f}s；上传+OCR {totals['ocr_http_seconds']:.3f}s（OCR首次加载 {totals['ocr_model_load_seconds']:.3f}s）；TTS实际合成 {totals['tts_synthesis_seconds']:.3f}s；ASR {totals['asr_seconds']:.3f}s（首次加载 {totals['asr_model_load_seconds']:.3f}s）。共 {totals['units']} 个单元、{totals['attempts']} 次合成/检查，恢复 {totals['recovered_units']} 个，待复核继续 {totals['unverified_units']} 个，HTTP全部200：{totals['all_http_200']}。</p><p>单元HTTP中位数 {totals['unit_http_median_seconds']:.3f}s，P95 {totals['unit_http_p95_seconds']:.3f}s。合成与ASR之和可能略小于HTTP时间，差值为Python调度、WAV拼接、响应传输与毫秒取整。</p><h2>逐单元</h2><table><tr><th>单元</th><th>TTS</th><th>ASR</th><th>其他</th><th>HTTP总计</th><th>尝试</th><th>结果</th></tr>{unit_rows}</table><p><a href='summary.json'>汇总</a> · <a href='units.json'>逐单元数值</a> · <a href='events.json'>无正文事件</a></p></html>"""
    (OUT/'index.html').write_text(doc,encoding='utf-8')
    print('REPORT',OUT/'index.html',flush=True)

if __name__=='__main__':main()
