# 固定自定义分段，只比较两套角色模型；不读取图片。
import argparse,contextlib,difflib,hashlib,html,io,json,shutil,time,wave
from pathlib import Path
import requests
from tools.compare_luoxi_cuts import switch,MODELS
from tools.full_volume_report import norm
from server.core import normalize_speech_text
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'~outputs-intermediate/evidence/custom-split-models-20260920';SOURCE=ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/short-text-recovery-20260920'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 global OUT
 parser=argparse.ArgumentParser();parser.add_argument('--no-repetition-penalty',action='store_true');args=parser.parse_args()
 gap=.5 if args.no_repetition_penalty else 0.0
 if args.no_repetition_penalty:OUT=ROOT/'~outputs-intermediate/evidence'/('three-pages-no-penalty-'+time.strftime('%Y%m%d-%H%M%S'))
 OUT.mkdir(exist_ok=True)
 if (OUT/'summary.json').exists():raise RuntimeError('已有完整证据，请使用新目录')
 cfg=read(ROOT/'config/reader.json');url=cfg['tts_url'];pages={n:read(SOURCE/n/'tts-input.json') for n in ('0009','0012','0041')}
 pages={name:[dict(item,text=normalize_speech_text(item['text'])) for item in items] for name,items in pages.items()}
 save(OUT/'inputs.json',pages)
 params=dict(text_lang='zh',prompt_lang='zh',prompt_text=cfg['reference_text'],text_split_method='cut0',batch_size=1,parallel_infer=False,seed=42,media_type='wav',streaming_mode=False,speed_factor=1.0,fragment_interval=.3,top_k=5,top_p=1.0,temperature=1.0,repetition_penalty=1.0 if args.no_repetition_penalty else 1.35,sample_steps=32,super_sampling=False)
 save(OUT/'parameters.json',params);results=[]
 for name,items in pages.items():
  folder=OUT/name;folder.mkdir(exist_ok=True)
  cards=''.join('<section><b>第 '+str(x['request'])+' 段 · 板块 '+str(x['panel_id']+1)+'</b><p>'+html.escape(x['text'])+'</p></section>' for x in items)
  (folder/'tts-input.html').write_text('<!doctype html><meta charset="utf-8"><title>TTS输入与自定义切分</title><style>body{font:17px/1.8 system-ui;max-width:900px;margin:30px auto}section{padding:14px;margin:12px;background:#f4f6fa}</style><h1>'+name+' · TTS输入与自定义切分</h1><p>V2ProPlus和V4共用以下输入，每框一次请求。分镜板块→中文句号；不额外补逗号。</p>'+cards,encoding='utf-8')
 def synth(payload):
  t=time.perf_counter();r=requests.post(url+'/tts',json=payload,timeout=300);r.raise_for_status()
  with wave.open(io.BytesIO(r.content)) as w:
   fmt=(w.getnchannels(),w.getsampwidth(),w.getframerate());frames=w.readframes(w.getnframes());assert len(frames)==w.getnframes()*fmt[0]*fmt[1] and len(frames)>0
   seconds=w.getnframes()/w.getframerate()
  return r.content,frames,fmt,dict(request_seconds=time.perf_counter()-t,audio_seconds=seconds)
 try:
  for version in ('v2ProPlus','v4'):
   switch(version,url,OUT)
   ref=OUT/(version+'-reference.wav');shutil.copyfile(cfg['reference_audio'],ref);assert sha(ref)==sha(Path(cfg['reference_audio']))
   base=dict(params,ref_audio_path=str(ref.resolve()))
   _,_,_,warm=synth(dict(base,text='你好，这是语音测试。'));save(OUT/('warmup-'+version+'.json'),warm)
   for name,items in pages.items():
    parts=[];fmt=None;seconds=0;elapsed=0
    for item in items:
     folder=OUT/name/'units'/str(item['request']).zfill(3);folder.mkdir(parents=True,exist_ok=True);payload=dict(base,text=item['text']);audio,frames,current,stats=synth(payload);assert fmt is None or fmt==current;fmt=current;parts.append(frames);seconds+=stats['audio_seconds'];elapsed+=stats['request_seconds']
     target=folder/(version+'.wav');target.write_bytes(audio);save(folder/(version+'.json'),dict(payload=payload,audio_sha256=sha(target),**stats));print('SYNTH',version,name,item['request'],'/',len(items),flush=True)
    audio=OUT/name/(version+'.wav')
    with wave.open(str(audio),'wb') as w:w.setnchannels(fmt[0]);w.setsampwidth(fmt[1]);w.setframerate(fmt[2]);w.writeframes((b'\x00'*(round(gap*fmt[2])*fmt[0]*fmt[1])).join(parts))
    results.append(dict(page=name,version=version,units=len(items),request_seconds=elapsed,audio_seconds=seconds,playback_seconds=seconds+gap*(len(items)-1),sentence_gap_seconds=gap,audio_sha256=sha(audio)))
 finally:
  switch(cfg['model_version'],url,OUT)
 finish_report(pages,results,args.no_repetition_penalty)

def finish_report(pages,results,no_repetition_penalty):
 from funasr import AutoModel
 with (OUT/'asr.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):model=AutoModel(model=read(ROOT/'config/asr-validation.lock.json')['model_path'],device='cpu',disable_update=True,ncpu=4)
 for item in results:
  with (OUT/'asr.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):rec=model.generate(input=str(OUT/item['page']/(item['version']+'.wav')))
  expected=norm(''.join(x['text'] for x in pages[item['page']]));actual=norm(''.join(x.get('text','') for x in rec));blocks=difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_matching_blocks();start=max(0,len(expected)-20)
  item.update(aligned_fraction=sum(b.size for b in blocks)/len(expected),tail_fraction=sum(max(0,min(b.a+b.size,len(expected))-max(b.a,start)) for b in blocks if b.size)/max(1,len(expected)-start),recognition=rec)
 save(OUT/'results.json',results);verify_output(OUT);summary={}
 for version in ('v2ProPlus','v4'):
  rs=[r for r in results if r['version']==version];summary[version]=dict(pages=3,units=sum(r['units'] for r in rs),mean_alignment=sum(r['aligned_fraction'] for r in rs)/3,mean_tail_alignment=sum(r['tail_fraction'] for r in rs)/3,request_seconds=sum(r['request_seconds'] for r in rs),audio_seconds=sum(r['audio_seconds'] for r in rs))
 save(OUT/'summary.json',summary)
 table='<table><tr><th>模型</th><th>平均全文对齐</th><th>末20字对齐</th><th>总合成耗时</th></tr>'+''.join(f'<tr><td>{v}</td><td>{r["mean_alignment"]:.1%}</td><td>{r["mean_tail_alignment"]:.1%}</td><td>{r["request_seconds"]:.2f}s</td></tr>' for v,r in summary.items())+'</table>'
 rows=[]
 for name,items in pages.items():
  cells=[]
  for version in ('v2ProPlus','v4'):
   r=next(x for x in results if x['page']==name and x['version']==version);cells.append(f'<td>全文 {r["aligned_fraction"]:.1%}；末尾 {r["tail_fraction"]:.1%}<br>合成 {r["request_seconds"]:.2f}s<br><audio controls preload="none" src="{name}/{version}.wav"></audio></td>')
  rows.append('<tr><th>'+name+'<br><a href="'+name+'/tts-input.html">TTS输入与切分（'+str(len(items))+'段）</a></th>'+''.join(cells)+'</tr>')
 report_note=('<p>本轮仅关闭重复惩罚（1.35→1.0），原字词、标点及自定义切分不变。整页试听在各段间加入0.5秒静音；这只是播放停顿，不修改TTS输入。正式默认参数未修改。</p>' if no_repetition_penalty else '')
 (OUT/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>自定义切分：洛茜V2ProPlus与V4</title><style>body{font:16px/1.8 system-ui;margin:32px}td,th{padding:16px;border:1px solid #ddd;text-align:left}table{border-collapse:collapse}audio{width:300px}</style><h1>同一自定义切分：洛茜V2ProPlus / V4</h1>'+report_note+'<p>三页共17段，两个版本使用完全相同的已确认文字和分段（板块→中文句号、不额外加逗号、包含本次补漏）。只比较模型版本，34段音频均为本次重新合成；播放器为各段按顺序拼接。</p>'+table+'<table><tr><th>页面与输入</th><th>V2ProPlus</th><th>V4</th></tr>'+''.join(rows)+'</table><p>固定同一中文参考音频、seed42、top_k5和串行参数。V2ProPlus为e20/e20，V4为e10/e16；不是对整个架构版本的普遍结论。每版本预热一次，耗时不含加载/预热/OCR/ASR。先V2ProPlus后V4，每段仅一次，未随机交错。ASR只是覆盖筛查，不是发音准确率或音质评分，我没有亲耳确认。没有查看图片，本轮未安卓实测。测试后恢复原默认V2ProPlus。</p><p><a href="results.json">结果证据</a> · <a href="parameters.json">固定请求参数</a></p><script>document.addEventListener("play",e=>{document.querySelectorAll("audio").forEach(a=>{if(a!==e.target)a.pause()})},true)</script></html>',encoding='utf-8')
 print(json.dumps(summary),flush=True);print('REPORT',str(OUT/'index.html'),flush=True)

def verify_output(folder):
 # 验证两模型请求一致、合成WAV完整以及整页只增加指定静音。
 pages=read(folder/'inputs.json');params=read(folder/'parameters.json');verified=0
 for result in read(folder/'results.json'):
  name=result['page'];version=result['version'];parts=[];fmt=None
  for item in pages[name]:
   unit=folder/name/'units'/str(item['request']).zfill(3);record=read(unit/(version+'.json'));payload=record['payload']
   assert payload['text']==item['text']
   assert {k:v for k,v in payload.items() if k not in ('text','ref_audio_path')}==params
   audio=unit/(version+'.wav');assert sha(audio)==record['audio_sha256']
   with wave.open(str(audio)) as w:
    current=(w.getnchannels(),w.getsampwidth(),w.getframerate());assert fmt is None or fmt==current;fmt=current
    frames=w.readframes(w.getnframes());assert len(frames)==w.getnframes()*fmt[0]*fmt[1];parts.append(frames)
   verified+=1
  expected=(b'\x00'*(round(result.get('sentence_gap_seconds',0)*fmt[2])*fmt[0]*fmt[1])).join(parts)
  audio=folder/name/(version+'.wav');assert sha(audio)==result['audio_sha256']
  with wave.open(str(audio)) as w:assert w.readframes(w.getnframes())==expected
 assert sha(folder/'v2ProPlus-reference.wav')==sha(folder/'v4-reference.wav')
 save(folder/'verification.json',dict(segment_audio_verified=verified,page_audio_verified=6,identical_text_and_parameters=True,reference_bytes_equal=True,only_requested_silence_added=True,images_viewed=False,android_tested=False))

if __name__=='__main__':main()
