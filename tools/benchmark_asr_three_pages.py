# 同一批17个既有朗读单元通过正式HTTP对比ASR关闭/开启；不读取图片。
import hashlib,html,json,statistics,time,urllib.request,wave,io
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/three-pages-no-penalty-20260921-010001/inputs.json'
OUT=ROOT/'~outputs-intermediate/evidence/asr-speed-three-pages-20260921'
CFG=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'))
BASE='http://127.0.0.1:8765'
AUTH={'X-Reader-Token':CFG['token']}

def request(url,method='GET',body=None,headers=None,timeout=240):
 data=None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8')
 req=urllib.request.Request(BASE+url,data=data,headers={**AUTH,**(headers or {})},method=method)
 with urllib.request.urlopen(req,timeout=timeout) as response:return response.status,response.read()

def join_wav(parts,gap_seconds=.5):
 frames=[];fmt=None
 for body in parts:
  with wave.open(io.BytesIO(body),'rb') as stream:
   current=(stream.getnchannels(),stream.getsampwidth(),stream.getframerate())
   if fmt is not None and fmt!=current:raise ValueError('WAV格式不一致')
   fmt=current;frames.append(stream.readframes(stream.getnframes()))
 gap=b'\0'*(round(gap_seconds*fmt[2])*fmt[0]*fmt[1])
 out=io.BytesIO()
 with wave.open(out,'wb') as stream:
  stream.setparams((*fmt,0,'NONE','not compressed'));stream.writeframes(gap.join(frames))
 return out.getvalue()

def main():
 if OUT.exists():raise SystemExit('证据目录已存在，拒绝覆盖：'+str(OUT))
 OUT.mkdir(parents=True)
 pages=json.loads(SOURCE.read_text(encoding='utf-8'))
 results=[];page_audio={}
 for page,items in pages.items():
  page_audio[page]={'off':[],'all':[]}
  for index,item in enumerate(items):
   modes=('off','all') if index%2==0 else ('all','off')
   for mode in modes:
    status,raw=request('/pages/text','POST',{'text':item['text']},{'Content-Type':'application/json'},10)
    payload=json.loads(raw);page_id=payload['page_id']
    if payload['sentences'][0]['text']!=item['text']:raise AssertionError('HTTP入口改变了输入')
    try:
     started=time.perf_counter()
     status,body=request(f'/pages/{page_id}/audio/0',headers={'X-Reader-ASR-Check':mode})
     elapsed=time.perf_counter()-started
     trace_path=ROOT/'~outputs-intermediate/pages'/page_id/'001.tts.json'
     trace=json.loads(trace_path.read_text(encoding='utf-8'))
    finally:
     request(f'/pages/{page_id}','DELETE',timeout=10)
    unit=OUT/page/'units'/f"{item['request']:03d}";unit.mkdir(parents=True,exist_ok=True)
    (unit/(mode+'.wav')).write_bytes(body)
    (unit/(mode+'.json')).write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    attempts=sum(len(x.get('attempts',[])) for x in trace)
    recovered=any(x.get('recovered',False) for x in trace)
    results.append(dict(page=page,request=item['request'],mode=mode,status=status,seconds=round(elapsed,3),
                        bytes=len(body),audio_sha256=hashlib.sha256(body).hexdigest(),attempts=attempts,
                        recovered=recovered,asr_checked=all(x.get('asr_checked') is (mode=='all') for x in trace)))
    page_audio[page][mode].append(body)
    print('DONE',page,item['request'],mode,round(elapsed,3),'attempts',attempts,flush=True)
 for page,modes in page_audio.items():
  for mode,parts in modes.items():
   body=join_wav(parts);(OUT/page).mkdir(exist_ok=True)
   (OUT/page/(mode+'.wav')).write_bytes(body)
 (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 summary=[]
 for page in pages:
  row={'page':page,'units':len(pages[page])}
  for mode in ('off','all'):
   subset=[x for x in results if x['page']==page and x['mode']==mode]
   row[mode+'_seconds']=round(sum(x['seconds'] for x in subset),3)
   row[mode+'_recovered']=sum(x['recovered'] for x in subset)
   row[mode+'_attempts']=sum(x['attempts'] for x in subset)
  row['extra_seconds']=round(row['all_seconds']-row['off_seconds'],3)
  row['ratio']=round(row['all_seconds']/row['off_seconds'],3)
  summary.append(row)
 total={mode:round(sum(x['seconds'] for x in results if x['mode']==mode),3) for mode in ('off','all')}
 lookup={(x['page'],x['request'],x['mode']):x for x in results}
 pairs=[(lookup[(page,item['request'],'off')],lookup[(page,item['request'],'all')]) for page,items in pages.items() for item in items]
 normal=[pair for pair in pairs if not pair[1]['recovered']]
 verification=dict(pages=3,units=17,http_audio_requests=34,all_status_200=all(x['status']==200 for x in results),
                   all_units_checked=all(x['asr_checked'] for x in results if x['mode']=='all'),
                   off_units_unchecked=all(x['asr_checked'] for x in results if x['mode']=='off'),
                   off_seconds=total['off'],all_seconds=total['all'],extra_seconds=round(total['all']-total['off'],3),
                   ratio=round(total['all']/total['off'],3),
                   recovered_units=sum(x['recovered'] for x in results if x['mode']=='all'),
                   identical_audio_without_recovery=sum(off['audio_sha256']==on['audio_sha256'] for off,on in pairs),
                   non_recovered_units=len(normal),
                   non_recovered_off_seconds=round(sum(off['seconds'] for off,on in normal),3),
                   non_recovered_all_seconds=round(sum(on['seconds'] for off,on in normal),3),
                   non_recovered_ratio=round(sum(on['seconds'] for off,on in normal)/sum(off['seconds'] for off,on in normal),3),
                   off_unit_median_seconds=round(statistics.median(x['seconds'] for x in results if x['mode']=='off'),3),
                   all_unit_median_seconds=round(statistics.median(x['seconds'] for x in results if x['mode']=='all'),3),
                   images_viewed=False,ocr_rerun=False,android_tested=False,asr_warm=True,model=CFG['model_version'])
 (OUT/'summary.json').write_text(json.dumps({'pages':summary,'total':total,'verification':verification},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 rows=''.join(f"<tr><th>{x['page']}</th><td>{x['units']}</td><td>{x['off_seconds']:.3f}s<br><audio controls preload='none' src='{x['page']}/off.wav'></audio></td><td>{x['all_seconds']:.3f}s<br><audio controls preload='none' src='{x['page']}/all.wav'></audio></td><td>+{x['extra_seconds']:.3f}s · {x['ratio']:.2f}倍</td><td>{x['all_recovered']}</td></tr>" for x in summary)
 doc=f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>三页ASR开关速度对比</title><style>body{{font:16px/1.7 system-ui;max-width:1100px;margin:30px auto}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:12px}}audio{{width:250px}}</style><h1>三页ASR开关速度对比</h1><p>沿用已确认的0009、0012、0041共17个外部朗读单元；同一文字通过正式HTTP分别请求off/all。ASR已常驻，因此是日常热状态速度。未重新OCR、未查看图片、未进行安卓实测。</p><table><tr><th>页</th><th>单元</th><th>ASR关闭</th><th>ASR开启</th><th>差异</th><th>恢复单元</th></tr>{rows}</table><p><b>合计：</b>关闭 {total['off']:.3f}s；开启 {total['all']:.3f}s；增加 {verification['extra_seconds']:.3f}s，约 {verification['ratio']:.2f} 倍。所有34个音频请求均为HTTP 200。</p><p>{verification['non_recovered_units']}个无需恢复的单元：关闭 {verification['non_recovered_off_seconds']:.3f}s，开启 {verification['non_recovered_all_seconds']:.3f}s，约 {verification['non_recovered_ratio']:.2f} 倍；这些单元两种模式返回的音频字节完全相同。另{verification['recovered_units']}个单元触发恢复，返回修复后的音频并增加额外合成时间。</p><p>整页试听在外部单元间加入0.5秒静音，仅用于报告播放，不计入请求耗时。</p><p><a href='summary.json'>汇总数据</a> · <a href='results.json'>逐单元数据</a></p></html>"""
 (OUT/'index.html').write_text(doc,encoding='utf-8')
 print('REPORT',OUT/'index.html',flush=True)
if __name__=='__main__':main()
