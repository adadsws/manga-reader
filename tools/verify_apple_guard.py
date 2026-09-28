# 正式服务函数验收：两模型同一句，记录完整性检查、内部重试和最终WAV。
import hashlib,html,json,shutil,time,wave
from pathlib import Path
from tools.compare_luoxi_cuts import switch
from tools.full_volume_report import norm
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/apple-guard-fix-20260921'
TEXT='那是什么...嗯？是苹果啊。'
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
 OUT.mkdir(exist_ok=True)
 from server import app as reader
 from server.tts_guard import coverage,recognize
 cfg=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'));results=[]
 original_ref=reader.settings['reference_audio']
 try:
  for version in ('v2ProPlus','v4'):
   switch(version,cfg['tts_url'],OUT)
   ref=OUT/(version+'-reference.wav');shutil.copyfile(original_ref,ref);reader.settings['reference_audio']=str(ref)
   audit=[];started=time.perf_counter();body=reader.synthesize(TEXT,audit=audit);elapsed=time.perf_counter()-started
   audio=OUT/(version+'.wav');audio.write_bytes(body)
   final_text=recognize(body);check=coverage(TEXT,final_text)
   with wave.open(str(audio)) as stream:
    seconds=stream.getnframes()/stream.getframerate();assert stream.getnframes()>0
   item=dict(version=version,text=TEXT,audio_sha256=hashlib.sha256(body).hexdigest(),audio_seconds=seconds,request_seconds=elapsed,final_asr=final_text,final_coverage=check,audit=audit)
   assert check['passed'],item
   assert audit and audit[0]['recovered']
   assert ''.join(x['text'] for x in audit[0]['requests'])==TEXT
   save(OUT/(version+'.json'),item);results.append(item);print('PASS',version,final_text,round(seconds,2),len(audit[0]['attempts']),flush=True)
 finally:
  reader.settings['reference_audio']=original_ref;switch(cfg['model_version'],cfg['tts_url'],OUT)
 save(OUT/'results.json',results)
 cards=[]
 for r in results:
  cards.append('<section><h2>'+r['version']+'</h2><audio controls preload="none" src="'+r['version']+'.wav"></audio><p>最终覆盖检查：通过；内部尝试 '+str(len(r['audit'][0]['attempts']))+' 次；接受 '+str(len(r['audit'][0]['requests']))+' 段。</p><p><a href="'+r['version']+'.json">完整请求、重试与转写记录</a></p></section>')
 (OUT/'tts-input.html').write_text('<!doctype html><meta charset="utf-8"><title>TTS输入</title><style>body{font:17px/1.8 system-ui;max-width:850px;margin:30px auto}section{padding:16px;background:#f4f6fa}</style><h1>实际外部TTS单元</h1><section><p>'+html.escape(TEXT)+'</p></section><p>外部仍是一段；检测到首次音频覆盖不足后，内部沿省略号保护合成并重新拼为一份WAV。</p>',encoding='utf-8')
 (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>苹果短句漏读修复</title><style>body{font:16px/1.8 system-ui;max-width:950px;margin:30px auto}section{padding:16px;margin:16px 0;border:1px solid #ddd}audio{width:360px}</style><h1>苹果短句漏读修复验收</h1><p><a href="tts-input.html">实际输入与内部保护说明</a></p>'+''.join(cards)+'<p>前端完整文本进入模型；漏读由语义模型提前输出结束符造成。仅取消额外结束条件不能修复。正式修复在返回前检查覆盖，失败时沿省略号内部重试；外部分段不变。ASR用于机器验收，我没有亲耳确认。</p>',encoding='utf-8')
 save(OUT/'verification.json',dict(models=2,final_audio_passed=2,external_unit_unchanged=True,characters_preserved=True,images_viewed=False,android_tested=False))
 print('REPORT',OUT/'index.html',flush=True)
if __name__=='__main__':main()
