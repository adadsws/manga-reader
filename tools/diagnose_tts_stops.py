# 复用固定GPT-SoVITS流水线，观测EOS并验证单元内部保护；不修改上游快照。
import ast,contextlib,difflib,gc,inspect,json,os,re,shutil,sys,textwrap,time,wave
from pathlib import Path
from tools.compare_luoxi_cuts import MODELS
from tools.probe_tts_omissions import read,save
from tools.full_volume_report import norm
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/apple-root-cause-20260921'
def main():
 OUT.mkdir(exist_ok=True);view=ROOT/'~temp/gpt-sovits';cfg=OUT/'tts.yaml';shutil.copyfile(ROOT/'config/tts.yaml',cfg)
 os.chdir(view);sys.path.insert(0,str(view));sys.path.insert(0,str(view/'GPT_SoVITS'))
 import tools
 tools.__path__.append(str(view/"tools"))
 import torch
 from TTS_infer_pack.TTS import TTS,TTS_Config
 from AR.models.t2s_model import Text2SemanticDecoder
 original=Text2SemanticDecoder.infer_panel_naive;source=textwrap.dedent(inspect.getsource(original))
 observed=source.replace('        if stop:\n','        if stop:\n            print("READER_STOP", dict(step=idx, prefix=prefix_len, tokens=int(y.shape[1]-prefix_len), sampled_eos=bool(samples[0,0]==self.EOS), argmax_eos=bool(torch.argmax(logits,dim=-1)[0]==self.EOS), early_stop_limit=early_stop_num))\n')
 assert observed!=source
 methods={}
 for strategy in ('baseline','sample_eos'):
  code=observed
  if strategy=='sample_eos':code=code.replace('torch.argmax(logits, dim=-1)[0] == self.EOS or samples[0, 0] == self.EOS','samples[0, 0] == self.EOS')
  namespace={};exec(compile(code,'<reader-eos-observation>','exec'),original.__globals__,namespace);methods[strategy]=namespace['infer_panel_naive']
 with (OUT/'load.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):pipe=TTS(TTS_Config(str(cfg)))
 results=[]
 for version in ('v2ProPlus','v4'):
  with (OUT/'load.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
   pipe.init_t2s_weights(str(ROOT/'models/gpt-sovits/洛茜'/MODELS[version][0]));pipe.init_vits_weights(str(ROOT/'models/gpt-sovits/洛茜'/MODELS[version][1]))
  ref=OUT/(version+'-reference.wav');shutil.copyfile(read(ROOT/'config/reader.json')['reference_audio'],ref)
  base=read(ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/three-pages-no-penalty-20260921-010001/0009/units/006'/ (version+'.json'))['payload'];base['ref_audio_path']=str(ref)
  for strategy in ('baseline','sample_eos','ellipsis_guard','punctuation_guard'):
   Text2SemanticDecoder.infer_panel_naive=methods['sample_eos' if strategy=='sample_eos' else 'baseline']
   texts=[base['text']]
   if strategy=='ellipsis_guard':texts=re.findall(r'.+?(?:\.{3}|$)',base['text'])
   if strategy=='punctuation_guard':texts=re.findall(r'.+?(?:\.{3}|[？?]|$)',base['text'])
   assert ''.join(texts)==base['text']
   for seed in (42,0,7):
    folder=OUT/version/strategy;folder.mkdir(parents=True,exist_ok=True);name=str(seed);logpath=folder/(name+'.log');audio=folder/(name+'.wav');dest=folder/(name+'.json')
    if dest.exists():
     results.append(read(dest));continue
    parts=[];payloads=[];started=time.perf_counter()
    with logpath.open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
     for text in texts:
      payload=dict(base,text=text,seed=seed);payloads.append(payload)
      output=list(pipe.run(payload));assert len(output)==1
      sr,pcm=output[0];assert pcm.dtype.name=='int16';parts.append(pcm.tobytes())
    with wave.open(str(audio),'wb') as w:w.setparams((1,2,sr,0,'NONE','not compressed'));w.writeframes(b''.join(parts))
    logs=logpath.read_text(encoding='utf-8');stops=[ast.literal_eval(x) for x in re.findall(r'READER_STOP (\{[^\r\n]*\})',logs)]
    item=dict(version=version,strategy=strategy,seed=seed,payloads=payloads,seconds=sum(len(x) for x in parts)/2/sr,stops=stops,request_seconds=time.perf_counter()-started)
    save(dest,item);results.append(item);print('SYNTH',version,strategy,seed,round(item['seconds'],2),stops,flush=True)
 del pipe;gc.collect();torch.cuda.empty_cache()
 from funasr import AutoModel
 with (OUT/'asr.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):asr=AutoModel(model=read(ROOT/'config/asr-validation.lock.json')['model_path'],device='cpu',disable_update=True,ncpu=4)
 for r in results:
  p=OUT/r['version']/r['strategy']/str(r['seed'])
  with (OUT/'asr.log').open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):rec=asr.generate(input=str(p.with_suffix('.wav')))
  r['stops']=[ast.literal_eval(x) for x in re.findall(r'READER_STOP (\{[^\r\n]*\})',p.with_suffix('.log').read_text(encoding='utf-8'))]
  actual=norm(''.join(x.get('text','') for x in rec));expected=norm(''.join(x['text'] for x in r['payloads']));r['asr']=dict(text=actual,head='那是什么' in actual,tail='是苹果' in actual,alignment=sum(x.size for x in difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_matching_blocks())/len(expected));save(p.with_suffix('.json'),r);print('ASR',r['version'],r['strategy'],r['seed'],r['asr'],flush=True)
 save(OUT/'results.json',results)
if __name__=='__main__':main()
