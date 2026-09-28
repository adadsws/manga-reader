# 固定三段反馈文本，比较推理参数；只处理已有文本和音频。
import argparse,contextlib,difflib,hashlib,html,io,json,re,shutil,time,wave
from pathlib import Path
import requests
from tools.compare_luoxi_cuts import switch
from tools.full_volume_report import norm
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'~outputs-intermediate/evidence/tts-omissions-20260920';SOURCE=ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/custom-split-models-20260920'
CASES={'apple':('0009','006'),'opening':('0012','003'),'repeat':('0041','004')}
VARIANTS={'baseline':{},'no_penalty':{'repetition_penalty':1.0},'wide':{'repetition_penalty':1.0,'top_k':15},'seed0':{'repetition_penalty':1.0,'top_k':15,'seed':0},'cool':{'repetition_penalty':1.0,'top_k':15,'temperature':.7}}
VARIANTS.update(plain={'repetition_penalty':1.0},soft_pause={'repetition_penalty':1.0},ellipsis_pause={'repetition_penalty':1.0})
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
 global OUT,CASES,VARIANTS
 parser=argparse.ArgumentParser();parser.add_argument('--suite',choices=('probe','regression','seeds','report','no_penalty'),default='probe');args=parser.parse_args()
 if args.suite=='no_penalty':
  OUT=ROOT/'~outputs-intermediate/evidence'/('three-lines-no-penalty-'+time.strftime('%Y%m%d-%H%M%S'));VARIANTS={'no_penalty':{'repetition_penalty':1.0}}
 if args.suite=='report':
  build_report();return
 if args.suite=='regression':
  OUT=OUT/'regression';CASES={next((k for k,v in CASES.items() if v==(page,p.name)),page+'-'+p.name):(page,p.name) for page in ('0009','0012','0041') for p in sorted((SOURCE/page/'units').iterdir()) if p.is_dir()};VARIANTS={k:VARIANTS[k] for k in ('baseline','no_penalty','soft_pause')}
 elif args.suite=='seeds':
  OUT=OUT/'seeds';VARIANTS={'soft_pause_seed0':{'repetition_penalty':1.0,'seed':0},'soft_pause_seed7':{'repetition_penalty':1.0,'seed':7},'no_penalty_seed0':{'repetition_penalty':1.0,'seed':0},'no_penalty_seed7':{'repetition_penalty':1.0,'seed':7}}
 OUT.mkdir(exist_ok=True);cfg=read(ROOT/'config/reader.json');results=[]
 try:
  for version in ('v2ProPlus','v4'):
   switch(version,cfg['tts_url'],OUT);ref=OUT/(version+'-reference.wav');shutil.copyfile(cfg['reference_audio'],ref)
   for case,(page,index) in CASES.items():
    old=SOURCE/page/'units'/index/(version+'.json');original=read(old)
    for variant,changes in VARIANTS.items():
     folder=OUT/version/case;folder.mkdir(parents=True,exist_ok=True);dest=folder/(variant+'.json');audio=dest.with_suffix('.wav')
     if dest.exists() and audio.exists():item=read(dest)
     else:
      payload=dict(original['payload'],ref_audio_path=str(ref.resolve()),**changes)
      # 实验性标点替换，保留所有字词和请求边界，不应用到正式服务。
      if variant=='ellipsis_pause':payload['text']=re.sub(r'…+|\.{3,}','，',payload['text'])
      if variant=='plain':payload['text']=re.sub(r'[，。！？…,.!?]+','',payload['text'])+'。'
      if variant.startswith('soft_pause'):payload['text']=re.sub(r'[！？…]+','，',payload['text']).rstrip('，。')+'。'
      if variant=='baseline':shutil.copyfile(old.with_suffix('.wav'),audio);elapsed=original['request_seconds'];payload=original['payload']
      else:
       start=time.perf_counter();r=requests.post(cfg['tts_url']+'/tts',json=payload,timeout=240);r.raise_for_status();audio.write_bytes(r.content);elapsed=time.perf_counter()-start
      with wave.open(str(audio)) as w:seconds=w.getnframes()/w.getframerate();assert w.getnframes()>0
      item=dict(version=version,case=case,variant=variant,payload=payload,request_seconds=elapsed,audio_seconds=seconds,audio_sha256=hashlib.sha256(audio.read_bytes()).hexdigest());save(dest,item)
     results.append(item);print('SYNTH',len(results),'/' + str(len(CASES)*len(VARIANTS)*2),version,case,variant,round(item['audio_seconds'],2),flush=True)
 finally:switch(cfg['model_version'],cfg['tts_url'],OUT)
 from funasr import AutoModel
 with (OUT/'asr.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):model=AutoModel(model=read(ROOT/'config/asr-validation.lock.json')['model_path'],device='cpu',disable_update=True,ncpu=4)
 for item in results:
  dest=OUT/item['version']/item['case']/(item['variant']+'.json')
  if 'asr' not in item:
   with (OUT/'asr.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):rec=model.generate(input=str(dest.with_suffix('.wav')))
   expected=norm(item['payload']['text']);actual=norm(''.join(x.get('text','') for x in rec));blocks=difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_matching_blocks()
   if item['case']=='apple':checks={'head':'那是什么' in actual,'tail':'是苹果啊' in actual}
   elif item['case']=='opening':checks={'opening':'要出门可以下次记得要好好联络' in actual}
   elif item['case']=='repeat':checks={'three_repeats':actual.count('不行')>=3,'tail':actual.endswith('不行')}
   else:checks={'tail':actual.endswith(expected[-4:])}
   item['asr']=dict(recognition=rec,normalized=actual,alignment=sum(b.size for b in blocks)/len(expected),checks=checks,all_targets=all(checks.values()));save(dest,item)
  print('ASR',item['version'],item['case'],item['variant'],round(item['asr']['alignment'],3),item['asr']['checks'],flush=True)
 save(OUT/'results.json',results)
 if args.suite=='no_penalty':
  build_three_line_report(results);print('REPORT',str(OUT/'index.html'),flush=True);return
 rows=[]
 for case in CASES:
  for version in ('v2ProPlus','v4'):
   cells=[]
   for v in VARIANTS:
    r=next(x for x in results if x['case']==case and x['version']==version and x['variant']==v);cells.append(f'<td>{r["asr"]["alignment"]:.1%} / 目标命中 {r["asr"]["all_targets"]}<br><audio controls preload="none" src="{version}/{case}/{v}.wav"></audio><br><a href="{version}/{case}/{v}.json">请求及转写</a></td>')
   rows.append('<tr><th>'+case+' / '+version+'</th>'+''.join(cells)+'</tr>')
 (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>三处吞句参数排查</title><style>body{font:16px/1.8 system-ui;margin:30px}td,th{border:1px solid #ccc;padding:12px}table{border-collapse:collapse}audio{width:230px}</style><h1>三处吞句：固定文字和自定义分段</h1><p>baseline=原试听；no_penalty=关闭重复惩罚；wide=再扩采样范围；seed0=更换随机种子；cool=降低温度；plain=去除内部标点；soft_pause=问号/感叹号/省略号替换为逗号；ellipsis_pause=只替换省略号。标点实验均关闭重复惩罚，保留字词和每段一次请求。ASR目标精确命中不是听感金标准，未命中也可能是识别错字。原文件均保留，未更改生产参数。</p><table><tr><th>案例</th>'+''.join('<th>'+v+'</th>' for v in VARIANTS)+'</tr>'+''.join(rows)+'</table>',encoding='utf-8')

def build_three_line_report(results):
 # 三句话独立试听，仅关闭重复惩罚，不改字词、标点或分段。
 labels={'apple':'第一句','opening':'第二句','repeat':'第三句'}
 style='<style>body{font:17px/1.8 system-ui;max-width:1050px;margin:30px auto}td,th{padding:16px;border:1px solid #ddd}table{border-collapse:collapse}audio{width:320px}section{background:#f4f6fa;padding:16px;margin:16px 0}</style>'
 rows=[];inputs=[]
 for case,(page,index) in CASES.items():
  pair=[r for r in results if r['case']==case];assert len(pair)==2
  assert pair[0]['payload']['text']==pair[1]['payload']['text']
  inputs.append('<section><b>'+labels[case]+' · 一次TTS请求</b><p>'+html.escape(pair[0]['payload']['text'])+'</p></section>')
  cells=[]
  for version in ('v2ProPlus','v4'):
   r=next(x for x in pair if x['version']==version)
   old=read(SOURCE/page/'units'/index/(version+'.json'))['payload'];payload=r['payload']
   assert {k:v for k,v in payload.items() if k not in ('repetition_penalty','ref_audio_path')}=={k:v for k,v in old.items() if k not in ('repetition_penalty','ref_audio_path')}
   assert payload['repetition_penalty']==1.0
   assert hashlib.sha256(Path(payload['ref_audio_path']).read_bytes()).digest()==hashlib.sha256(Path(old['ref_audio_path']).read_bytes()).digest()
   audio=OUT/version/case/'no_penalty.wav';assert hashlib.sha256(audio.read_bytes()).hexdigest()==r['audio_sha256']
   result='目标文字已检出' if r['asr']['all_targets'] else '目标文字未完整检出'
   cells.append(f'<td><audio controls preload="none" src="{version}/{case}/no_penalty.wav"></audio><br>ASR：{result}<br><a href="{version}/{case}/no_penalty.json">实际请求与转写</a></td>')
  rows.append('<tr><th>'+labels[case]+'</th>'+''.join(cells)+'</tr>')
 (OUT/'tts-input.html').write_text('<!doctype html><meta charset="utf-8"><title>三句TTS输入</title>'+style+'<h1>实际TTS输入与切分</h1><p>两版使用相同输入，每框一次请求，保留原标点。</p>'+''.join(inputs),encoding='utf-8')
 (OUT/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>关闭重复惩罚：三句试听</title>'+style+'<h1>关闭重复惩罚 · 三句试听</h1><p>6段均为本轮新合成。仅将 repetition_penalty 从1.35设为1.0，其他参数不变；保留原标点、自定义切分、seed42和top_k5。</p><p><a href="tts-input.html">查看实际TTS输入与切分（独立文件）</a></p><table><tr><th>句子</th><th>V2ProPlus</th><th>V4</th></tr>'+''.join(rows)+'</table><p>ASR仅作筛查，我没有亲耳确认。正式默认参数未更改；未查看图片、未安卓实测。</p><script>document.addEventListener("play",e=>document.querySelectorAll("audio").forEach(a=>{if(a!==e.target)a.pause()}),true)</script></html>',encoding='utf-8')
 save(OUT/'verification.json',dict(new_audio=6,only_repetition_penalty_changed=True,reference_bytes_equal=True,original_punctuation=True,custom_units_unchanged=True,images_viewed=False,android_tested=False))

def build_report():
 # 汇总实验，不把候选参数写入生产配置；输入正文保留在独立文件。
 from collections import defaultdict
 results=read(OUT/'results.json');reg=read(OUT/'regression/results.json');seeds=read(OUT/'seeds/results.json')
 labels={'apple':'苹果短句','opening':'长句开头','repeat':'末尾重复词'}
 style='<style>body{font:16px/1.8 system-ui;margin:28px;max-width:1250px}td,th{padding:12px;border:1px solid #ddd;vertical-align:top}table{border-collapse:collapse;margin:18px 0}audio{width:260px}section{padding:16px;background:#f5f7fa;margin:20px 0}a{color:#1460ad}</style>'
 rows=[]
 for case,label in labels.items():
  folder=OUT/case;folder.mkdir(exist_ok=True)
  texts=[]
  for variant,name in [('baseline','原始输入'),('no_penalty','关闭重复惩罚'),('soft_pause','关闭重复惩罚并替换内部强标点')]:
   pair=[r for r in results if r['case']==case and r['variant']==variant]
   assert len(pair)==2 and pair[0]['payload']['text']==pair[1]['payload']['text']
   texts.append('<section><b>'+name+' · 每框一次请求，两版共用</b><p>'+html.escape(pair[0]['payload']['text'])+'</p></section>')
  (folder/'tts-input.html').write_text('<!doctype html><meta charset="utf-8"><title>TTS输入与切分</title>'+style+'<h1>'+label+'：TTS输入与切分</h1><p>分段边界保持不变；soft_pause仅是标点实验，不是生产规则。</p>'+''.join(texts),encoding='utf-8')
  for version in ('v2ProPlus','v4'):
   cells=[]
   for variant in ('baseline','no_penalty','soft_pause'):
    r=next(x for x in results if x['case']==case and x['version']==version and x['variant']==variant)
    hit='目标检出' if r['asr']['all_targets'] else '目标未完整检出'
    cells.append(f'<td>{hit}<br><audio controls preload="none" src="{version}/{case}/{variant}.wav"></audio><br><a href="{version}/{case}/{variant}.json">请求与转写</a></td>')
   rows.append('<tr><th>'+label+' / '+version+'<br><a href="'+case+'/tts-input.html">TTS输入与切分</a></th>'+''.join(cells)+'</tr>')
 metrics=[]
 for version in ('v2ProPlus','v4'):
  for variant in ('no_penalty','soft_pause'):
   pairs=[]
   for r in reg:
    if r['version']==version and r['variant']==variant:
     b=next(x for x in reg if x['version']==version and x['case']==r['case'] and x['variant']=='baseline');pairs.append((b,r))
   improved=sum(r['asr']['alignment']>b['asr']['alignment']+1e-9 for b,r in pairs);worse=sum(r['asr']['alignment']<b['asr']['alignment']-1e-9 for b,r in pairs)
   trials=[r for r in seeds if r['version']==version and r['variant'].startswith(variant)]+[r for r in results if r['version']==version and r['variant']==variant]
   metrics.append(dict(version=version,variant=variant,units=len(pairs),alignment_improved=improved,alignment_worse=worse,alignment_unchanged=len(pairs)-improved-worse,target_cases_across_seeds=sum(r['asr']['all_targets'] for r in trials),total_target_cases=len(trials)))
 save(OUT/'summary.json',metrics)
 # 用字词、参考哈希和WAV帧完整性验证实验可比性，不读取漫画图片。
 verified=0
 for root,data in ((OUT,results),(OUT/'regression',reg),(OUT/'seeds',seeds)):
  for r in data:
   audio=root/r['version']/r['case']/(r['variant']+'.wav')
   assert hashlib.sha256(audio.read_bytes()).hexdigest()==r['audio_sha256']
   with wave.open(str(audio)) as w:
    assert len(w.readframes(w.getnframes()))==w.getnframes()*w.getnchannels()*w.getsampwidth()
   base=next(x for x in (reg if root.name=='regression' else results) if x['case']==r['case'] and x['version']==r['version'] and x['variant']=='baseline')
   assert norm(r['payload']['text'])==norm(base['payload']['text'])
   assert hashlib.sha256(Path(r['payload']['ref_audio_path']).read_bytes()).hexdigest()==hashlib.sha256(Path(base['payload']['ref_audio_path']).read_bytes()).hexdigest()
   verified+=1
 lock=read(ROOT/'config/upstream-files.lock.json');upstream=0
 for repo,files in lock.items():
  for name,digest in files.items():
   assert hashlib.sha256((ROOT/'reference'/repo/name).read_bytes()).hexdigest()==digest;upstream+=1
 save(OUT/'verification.json',dict(verified_audio_requests=verified,same_normalized_characters=True,same_reference_bytes=True,upstream_files=upstream,production_settings_unchanged=True,images_viewed=False,android_tested=False))
 detail='<table><tr><th>版本/候选</th><th>17段：改善 / 相同 / 下降</th><th>3个种子×3处目标检出</th></tr>'+''.join(f'<tr><td>{m["version"]} / {m["variant"]}</td><td>{m["alignment_improved"]} / {m["alignment_unchanged"]} / {m["alignment_worse"]}</td><td>{m["target_cases_across_seeds"]} / {m["total_target_cases"]}</td></tr>' for m in metrics)+'</table>'
 old=OUT/'index.html'
 if not (OUT/'parameters.html').exists():shutil.copyfile(old,OUT/'parameters.html')
 old.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>吞句复测：V2与V4</title>'+style+'<h1>三处吞句复测</h1><p>原请求包含完整字词；本轮定位在合成阶段。关闭重复惩罚在seed42下检出长句开头及最后一个“不行”，仍未解决苹果短句。替换内部强标点可使三处目标在seed42下均检出，但全17段回归有下降，不能作为全局可靠修复。正式配置尚未更改。</p><p>下面直接对照原音频和候选音频；完整输入放在独立文件。均保留原自定义请求边界，没有改成cut数字试验，也没有补录、拼接漏字。</p><table><tr><th>片段</th><th>原试听</th><th>只关闭重复惩罚</th><th>再将问号/叹号/省略号换为逗号（实验）</th></tr>'+''.join(rows)+'</table><h2>回归与稳定性</h2>'+detail+'<p>改善/下降按逐段ASR字符对齐统计，不是人工听感评分；目标未检出也可能是ASR误识。种子为42、0、7，每组合一次；seed42使用首轮数据，另外两次独立生成。参数扫描8组×3处×2版；回归3组×17段×2版；种子补测2组×3处×2种子×2版。基线音频复制原报告，候选新合成。</p><p>字词、参考音频和分段保持一致，只有明确标注的标点候选改变标点。未查看图片，未进行安卓实测。0～3秒句间停顿是播放器等待参数，不会让模型补读漏字。</p><p><a href="parameters.html">全部参数实验</a> · <a href="regression/index.html">17段回归试听</a> · <a href="seeds/index.html">种子稳定性试听</a> · <a href="summary.json">汇总数据</a></p><script>document.addEventListener("play",e=>document.querySelectorAll("audio").forEach(a=>{if(a!==e.target)a.pause()}),true)</script></html>',encoding='utf-8')
 print(json.dumps(metrics),flush=True)

if __name__=='__main__':main()
