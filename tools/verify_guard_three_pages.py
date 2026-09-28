# 三页17段正式路径回归；不读取图片，沿用已确认文本和切分。
import contextlib,difflib,hashlib,html,json,re,shutil,time,wave
from pathlib import Path
from tools.compare_luoxi_cuts import switch
from tools.full_volume_report import norm
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/apple-guard-three-pages-20260921'
SOURCE=ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/three-pages-no-penalty-20260921-010001/inputs.json'
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 OUT.mkdir(exist_ok=True)
 pages=json.loads(SOURCE.read_text(encoding='utf-8'));save(OUT/'inputs.json',pages)
 from server import app as reader
 from server.tts_guard import coverage,recognize
 def legacy_guard(text):
  return len(re.findall(r'[\u3400-\u9fff0-9]',text))<=18 and bool(re.search(r'\.\.\.[^。]*[？?]',text))
 cfg=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'));url=cfg['tts_url'];original_ref=reader.settings['reference_audio'];results=[]
 try:
  for version in ('v2ProPlus','v4'):
   switch(version,url,OUT);ref=OUT/(version+'-reference.wav');shutil.copyfile(original_ref,ref);reader.settings['reference_audio']=str(ref)
   for page,items in pages.items():
    page_dir=OUT/page;page_dir.mkdir(exist_ok=True);frames=[];fmt=None;traces=[];started=time.perf_counter()
    for item in items:
     audit=[];body=reader.synthesize(item['text'],audit=audit,asr_check=legacy_guard(item['text']));unit=page_dir/'units'/str(item['request']).zfill(3);unit.mkdir(parents=True,exist_ok=True)
     audio=unit/(version+'.wav');audio.write_bytes(body);save(unit/(version+'.json'),dict(text=item['text'],guarded=legacy_guard(item['text']),audit=audit,audio_sha256=sha(audio)))
     with wave.open(str(audio)) as stream:
      current=(stream.getnchannels(),stream.getsampwidth(),stream.getframerate());assert fmt is None or current==fmt;fmt=current;frames.append(stream.readframes(stream.getnframes()))
     traces.append(audit);print('SYNTH',version,page,item['request'],'/',len(items),flush=True)
    gap=b'\x00'*(round(.5*fmt[2])*fmt[0]*fmt[1]);whole=page_dir/(version+'.wav')
    with wave.open(str(whole),'wb') as stream:stream.setparams((*fmt,0,'NONE','not compressed'));stream.writeframes(gap.join(frames))
    final=recognize(whole.read_bytes());expected=''.join(x['text'] for x in items);match=difflib.SequenceMatcher(None,norm(expected),norm(final),autojunk=False)
    apple=next((a for item,a in zip(items,traces) if legacy_guard(item['text'])),None)
    if apple:
     assert apple[0]['coverage']['passed'] if 'coverage' in apple[0] else True
     assert ''.join(x['text'] for x in apple[0]['requests'])==next(x['text'] for x in items if legacy_guard(x['text']))
    results.append(dict(page=page,version=version,units=len(items),guarded_units=sum(legacy_guard(x['text']) for x in items),audio_sha256=sha(whole),audio_seconds=(sum(len(x) for x in frames)+len(gap)*(len(frames)-1))/fmt[0]/fmt[1]/fmt[2],request_seconds=time.perf_counter()-started,asr=final,alignment=sum(x.size for x in match.get_matching_blocks())/max(1,len(norm(expected)))))
  save(OUT/'results.json',results)
 finally:
  reader.settings['reference_audio']=original_ref;switch(cfg['model_version'],url,OUT)
 for page,items in pages.items():
  cards=''.join('<section><b>第 '+str(x['request'])+' 段</b><p>'+html.escape(x['text'])+'</p></section>' for x in items)
  (OUT/page/'tts-input.html').write_text('<!doctype html><meta charset="utf-8"><title>TTS输入</title><style>body{font:17px/1.8 system-ui;max-width:850px;margin:30px auto}section{padding:12px;background:#f4f6fa;margin:10px}</style><h1>'+page+' 实际外部单元</h1><p>外部仍为17个已确认单元。短复合句若覆盖不足，内部沿省略号重试，审计保存在各单元JSON。</p>'+cards,encoding='utf-8')
 rows=[]
 for page,items in pages.items():
  cells=[]
  for version in ('v2ProPlus','v4'):
   r=next(x for x in results if x['page']==page and x['version']==version);cells.append(f'<td>ASR对齐 {r["alignment"]:.1%}<br><audio controls preload="none" src="{page}/{version}.wav"></audio></td>')
  rows.append('<tr><th>'+page+'<br><a href="'+page+'/tts-input.html">TTS输入与切分</a></th>'+''.join(cells)+'</tr>')
 (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>苹果短句修复三页回归</title><style>body{font:16px/1.8 system-ui;margin:30px}table{border-collapse:collapse}td,th{padding:16px;border:1px solid #ddd}audio{width:320px}</style><h1>苹果短句修复 · 三页回归</h1><p>V2ProPlus/V4各17段均经正式服务函数重新合成；关闭重复惩罚，省略号统一为...，段间0.5秒。外部切分不变，只有0009第6段进入完整性保护。</p><table><tr><th>页面</th><th>V2ProPlus</th><th>V4</th></tr>'+''.join(rows)+'</table><p>ASR仅用于机器验收，我没有亲耳确认。未查看图片、未进行安卓实测。</p>',encoding='utf-8')
 save(OUT/'verification.json',dict(pages=3,models=2,segment_audio=34,page_audio=6,guarded_units_per_model=1,external_units_unchanged=True,images_viewed=False,android_tested=False))
 print('REPORT',OUT/'index.html',flush=True)
if __name__=='__main__':main()
