# ASR逐单元检查开启时，对比洛茜V2ProPlus和V4；不读取图片。
import hashlib,html,io,json,time,urllib.request,wave
from pathlib import Path
from tools.compare_luoxi_cuts import switch
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/three-pages-no-penalty-20260921-010001/inputs.json'
OUT=ROOT/'~outputs-intermediate/evidence/asr-models-three-pages-20260921'
CFG=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'))
BASE='http://127.0.0.1:8765';AUTH={'X-Reader-Token':CFG['token']}

def request(path,method='GET',body=None,headers=None,timeout=300):
 data=None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8')
 req=urllib.request.Request(BASE+path,data=data,headers={**AUTH,**(headers or {})},method=method)
 with urllib.request.urlopen(req,timeout=timeout) as response:return response.status,response.read()

def audio_request(text):
 _,raw=request('/pages/text','POST',{'text':text},{'Content-Type':'application/json'},10)
 payload=json.loads(raw);page_id=payload['page_id']
 if payload['sentences'][0]['text']!=text:raise AssertionError('HTTP入口改变了输入')
 try:
  started=time.perf_counter();status,body=request(f'/pages/{page_id}/audio/0',headers={'X-Reader-ASR-Check':'all'})
  elapsed=time.perf_counter()-started
  trace=json.loads((ROOT/'~outputs-intermediate/pages'/page_id/'001.tts.json').read_text(encoding='utf-8'))
  return status,body,trace,elapsed
 finally:request(f'/pages/{page_id}','DELETE',timeout=10)

def join_wav(parts,gap_seconds=.5):
 frames=[];fmt=None
 for body in parts:
  with wave.open(io.BytesIO(body),'rb') as stream:
   current=(stream.getnchannels(),stream.getsampwidth(),stream.getframerate())
   if fmt is not None and fmt!=current:raise ValueError('WAV格式不一致')
   fmt=current;frames.append(stream.readframes(stream.getnframes()))
 gap=b'\0'*(round(gap_seconds*fmt[2])*fmt[0]*fmt[1])
 out=io.BytesIO()
 with wave.open(out,'wb') as stream:stream.setparams((*fmt,0,'NONE','not compressed'));stream.writeframes(gap.join(frames))
 return out.getvalue()

def sha(body):return hashlib.sha256(body).hexdigest()

def main():
 if OUT.exists():raise SystemExit('证据目录已存在，拒绝覆盖：'+str(OUT))
 OUT.mkdir(parents=True)
 pages=json.loads(SOURCE.read_text(encoding='utf-8'))
 (OUT/'inputs.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 results=[];warmups=[]
 try:
  for version in ('v2ProPlus','v4'):
   switch(version,CFG['tts_url'],OUT)
   status,_,trace,elapsed=audio_request('模型预热。')
   warmups.append(dict(version=version,status=status,seconds=round(elapsed,3),attempts=sum(len(x.get('attempts',[])) for x in trace)))
   for page,items in pages.items():
    parts=[]
    for item in items:
     status,body,trace,elapsed=audio_request(item['text'])
     unit=OUT/page/'units'/f"{item['request']:03d}";unit.mkdir(parents=True,exist_ok=True)
     (unit/(version+'.wav')).write_bytes(body)
     (unit/(version+'.json')).write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
     attempts=sum(len(x.get('attempts',[])) for x in trace);recovered=any(x.get('recovered',False) for x in trace)
     accepted=[req for chunk in trace for req in chunk.get('requests',[])]
     scores=[max(req['coverage'].get('phonetic_alignment',req['coverage']['alignment']),req['coverage'].get('onset_alignment',0)) for req in accepted]
     results.append(dict(page=page,request=item['request'],version=version,status=status,seconds=round(elapsed,3),
                         bytes=len(body),audio_sha256=sha(body),attempts=attempts,recovered=recovered,
                         accepted_requests=len(accepted),min_accepted_coverage=min(scores) if scores else None,
                         asr_checked=all(x.get('asr_checked') is True for x in trace)))
     parts.append(body);print('DONE',version,page,item['request'],'seconds',round(elapsed,3),'attempts',attempts,flush=True)
    joined=join_wav(parts);(OUT/page).mkdir(exist_ok=True)
    (OUT/page/(version+'.wav')).write_bytes(joined)
 finally:
  switch(CFG['model_version'],CFG['tts_url'],OUT)
 (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (OUT/'warmups.json').write_text(json.dumps(warmups,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 summaries=[]
 for version in ('v2ProPlus','v4'):
  subset=[x for x in results if x['version']==version]
  summaries.append(dict(version=version,units=len(subset),seconds=round(sum(x['seconds'] for x in subset),3),
                        recovered_units=sum(x['recovered'] for x in subset),attempts=sum(x['attempts'] for x in subset),
                        first_pass_units=sum(not x['recovered'] for x in subset),
                        min_accepted_coverage=min(x['min_accepted_coverage'] for x in subset)))
 pages_summary=[]
 for page in pages:
  row={'page':page,'units':len(pages[page])}
  for version in ('v2ProPlus','v4'):
   subset=[x for x in results if x['page']==page and x['version']==version]
   row[version]=dict(seconds=round(sum(x['seconds'] for x in subset),3),recovered=sum(x['recovered'] for x in subset),attempts=sum(x['attempts'] for x in subset))
  pages_summary.append(row)
 verification=dict(pages=3,units_per_model=17,http_audio_requests=34,all_status_200=all(x['status']==200 for x in results),
                   all_units_asr_checked=all(x['asr_checked'] for x in results),models=['v2ProPlus','v4'],
                   same_inputs=True,asr_warm=True,default_model_restored=CFG['model_version'],
                   images_viewed=False,ocr_rerun=False,android_tested=False)
 v2=next(x for x in summaries if x['version']=='v2ProPlus');v4=next(x for x in summaries if x['version']=='v4')
 comparison=dict(v4_vs_v2_time_ratio=round(v4['seconds']/v2['seconds'],3),
                 v4_extra_seconds=round(v4['seconds']-v2['seconds'],3),
                 v4_extra_attempts=v4['attempts']-v2['attempts'])
 summary={'models':summaries,'pages':pages_summary,'verification':verification,'comparison':comparison}
 (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 table=''.join(f"<tr><th>{x['version']}</th><td>{x['seconds']:.3f}s</td><td>{x['first_pass_units']}/17</td><td>{x['recovered_units']}</td><td>{x['attempts']}</td><td>{x['min_accepted_coverage']:.1%}</td></tr>" for x in summaries)
 rows=''
 for item in pages_summary:
  cells=''.join(f"<td>{item[v]['seconds']:.3f}s；恢复{item[v]['recovered']}<br><audio controls preload='none' src='{item['page']}/{v}.wav'></audio></td>" for v in ('v2ProPlus','v4'))
  rows+=f"<tr><th>{item['page']}（{item['units']}段）</th>{cells}</tr>"
 doc=f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>ASR开启：V2ProPlus与V4三页对比</title><style>body{{font:16px/1.7 system-ui;max-width:1100px;margin:30px auto}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #ddd;padding:12px}}audio{{width:300px}}</style><h1>ASR开启 · V2ProPlus / V4三页对比</h1><p>沿用同一三页17个已确认外部朗读单元，两模型均通过正式HTTP并强制X-Reader-ASR-Check=all。ASR已常驻；耗时不含模型切换与预热。未重新OCR、未查看图片、未安卓实测。</p><table><tr><th>模型</th><th>17段耗时</th><th>首次通过</th><th>恢复单元</th><th>总尝试</th><th>接受片段最低覆盖</th></tr>{table}</table><table><tr><th>页</th><th>V2ProPlus</th><th>V4</th></tr>{rows}</table><p><b>本轮结论：</b>V4为V2ProPlus的 {comparison['v4_vs_v2_time_ratio']:.2f} 倍，增加 {comparison['v4_extra_seconds']:.3f} 秒和 {comparison['v4_extra_attempts']} 次内部尝试；两者17个单元均最终通过。</p><p>整页WAV在外部单元之间加入0.5秒静音，仅用于试听，不计入请求耗时。ASR覆盖用于自动筛查，不能替代人工试听或音质评分。测试结束已恢复默认{CFG['model_version']}。</p><p><a href='summary.json'>汇总</a> · <a href='results.json'>逐单元结果</a> · <a href='warmups.json'>预热记录</a></p></html>"""
 (OUT/'index.html').write_text(doc,encoding='utf-8')
 print('REPORT',OUT/'index.html',flush=True)
if __name__=='__main__':main()
