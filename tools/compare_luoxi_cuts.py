# 同一原图OCR文本的六组原生TTS比较；不打开或显示图片。
import contextlib, difflib, hashlib, html, importlib.util, io, json, statistics, time, wave
from pathlib import Path
import requests
from tools.full_volume_report import norm
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/luoxi-cut-20260920'
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
MODELS={
 'v4': ('V4/洛茜-e10.ckpt','V4/洛茜_e16_s544_l32.pth'),
 'v2ProPlus': ('V2ProPlus/终末地_洛茜-e20.ckpt','V2ProPlus/终末地_洛茜_e20_s500.pth')}

def switch(version,url,out=None):
    responses=[]
    for endpoint,relative in zip(('set_gpt_weights','set_sovits_weights'),MODELS[version]):
        path=ROOT/'models/gpt-sovits/洛茜'/relative
        t=time.perf_counter()
        response=requests.get(url+'/'+endpoint,params={'weights_path':str(path)},timeout=180)
        record=dict(endpoint=endpoint,path=path.relative_to(ROOT).as_posix(),sha256=sha(path),status=response.status_code,response=response.json(),seconds=time.perf_counter()-t)
        responses.append(record)
        save((out or OUT)/('load-'+version+'.json'),responses)
        response.raise_for_status()
    print('MODEL_READY',version,flush=True)

def main():
    OUT.mkdir(exist_ok=True)
    cfg=read(ROOT/'config/reader.json'); url=cfg['tts_url']
    inputs=[]
    for path in sorted((ROOT/'secrets/manga').glob('*.jpg')):
        dest=OUT/(path.stem+'-ocr.json')
        if dest.exists(): item=read(dest)
        else:
            start=time.perf_counter()
            r=requests.post('http://127.0.0.1:8765/pages',headers={'X-Reader-Token':cfg['token'],'Content-Type':'image/jpeg'},data=path.read_bytes(),timeout=300)
            r.raise_for_status(); result=r.json()
            item=dict(id=path.stem,source=path.relative_to(ROOT).as_posix(),source_sha256=sha(path),ocr_seconds=time.perf_counter()-start,result=result,text=''.join(row['text'] for row in result['sentences']))
            item['text_sha256']=hashlib.sha256(item['text'].encode()).hexdigest()
            save(dest,item)
            requests.delete('http://127.0.0.1:8765/pages/'+result['page_id'],headers={'X-Reader-Token':cfg['token']},timeout=30).raise_for_status()
        if item['source_sha256']!=sha(path): raise RuntimeError('Input changed: '+path.name)
        if not item['text']: raise RuntimeError('No recognized text: '+path.name)
        inputs.append(item)
        print('OCR_READY',path.name,'characters',len(item['text']),flush=True)
    if len(inputs)!=3: raise RuntimeError('Expected exactly three root JPEGs')
    save(OUT/'inputs.json',inputs)
    common=dict(text_lang='zh',prompt_lang='zh',ref_audio_path=cfg['reference_audio'],prompt_text=cfg['reference_text'],batch_size=1,parallel_infer=False,seed=42,media_type='wav',streaming_mode=False,speed_factor=1.0,fragment_interval=0.3,top_k=5,top_p=1.0,temperature=1.0,repetition_penalty=1.35,sample_steps=32,super_sampling=False)
    save(OUT/'parameters.json',common)
    spec=importlib.util.spec_from_file_location('fixed_cuts',ROOT/'reference/tts/GPT-SoVITS/GPT_SoVITS/TTS_infer_pack/text_segmentation_method.py')
    cuts=importlib.util.module_from_spec(spec); spec.loader.exec_module(cuts)
    def synth(payload):
        start=time.perf_counter(); r=requests.post(url+'/tts',json=payload,timeout=300); r.raise_for_status()
        with wave.open(io.BytesIO(r.content)) as w:
            info=dict(audio_seconds=w.getnframes()/w.getframerate(),sample_rate=w.getframerate(),channels=w.getnchannels(),frames=w.getnframes(),sample_width=w.getsampwidth())
            if not w.getnframes(): raise ValueError('empty WAV')
        return r.content,dict(info,request_seconds=time.perf_counter()-start)
    results=[]
    try:
        for version in MODELS:
            switch(version,url)
            warm=[]
            for method in ('cut0','cut1','cut3'):
                _,stats=synth(dict(common,text='你好，这是语音测试。现在准备开始。',text_split_method=method))
                warm.append(dict(method=method,**stats))
            save(OUT/('warmup-'+version+'.json'),warm)
            for i,case in enumerate(inputs):
                folder=OUT/case['id'];folder.mkdir(exist_ok=True)
                for method in (('cut0','cut1','cut3') if i%2==0 else ('cut3','cut1','cut0')):
                    dest=folder/(version+'-'+method+'.json');audio=dest.with_suffix('.wav')
                    if dest.exists() and audio.exists():
                        item=read(dest)
                        if item['text_sha256']!=case['text_sha256'] or item['payload']!=dict(common,text=case['text'],text_split_method=method) or item['audio_sha256']!=sha(audio):
                            raise RuntimeError('Cached evidence mismatch: '+str(dest))
                    else:
                        payload=dict(common,text=case['text'],text_split_method=method)
                        data,stats=synth(payload); audio.write_bytes(data)
                        item=dict(id=case['id'],version=version,method=method,payload=payload,text_sha256=case['text_sha256'],audio_sha256=sha(audio),raw_cut_output=getattr(cuts,method)(case['text']),raw_cut_note='原生切法输出；后续预处理仍会合并短于5字的片段、补标点和处理超长文本。',**stats)
                        save(dest,item)
                    results.append(item)
                    print('SYNTH',len(results),'/18',case['id'],version,method,round(item['request_seconds'],2),flush=True)
    finally:
        switch(cfg.get('model_version','v4'),url)
    from funasr import AutoModel
    (OUT/'validation').mkdir(exist_ok=True)
    logpath=OUT/'validation/asr-runtime.log'
    with logpath.open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        model=AutoModel(model=read(ROOT/'config/asr-validation.lock.json')['model_path'],device='cpu',disable_update=True,ncpu=4)
    for i,item in enumerate(results):
        dest=OUT/item['id']/(item['version']+'-'+item['method']+'.json')
        if 'asr' not in item:
            with logpath.open('a',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
                recognized=model.generate(input=str(dest.with_suffix('.wav')))
            expected=norm(item['payload']['text']);actual=norm(''.join(x.get('text','') for x in recognized))
            blocks=difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_matching_blocks();start=max(0,len(expected)-20)
            aligned=sum(b.size for b in blocks)/max(1,len(expected))
            tail=sum(max(0,min(b.a+b.size,len(expected))-max(b.a,start)) for b in blocks if b.size)/max(1,len(expected)-start)
            item['asr']=dict(recognition=recognized,aligned_fraction=aligned,tail_fraction=tail,unmatched_trailing=len(expected)-max((b.a+b.size for b in blocks if b.size),default=0),needs_review=aligned<.9 or tail<.8)
            save(dest,item)
        print('ASR',i+1,'/18',item['id'],item['version'],item['method'],round(item['asr']['aligned_fraction'],3),flush=True)
    summary={}
    names=[v+'-'+m for v in MODELS for m in ('cut0','cut1','cut3')]
    for name in names:
        rs=[r for r in results if r['version']+'-'+r['method']==name]
        summary[name]=dict(pages=len(rs),mean_alignment=statistics.mean(r['asr']['aligned_fraction'] for r in rs),mean_tail_alignment=statistics.mean(r['asr']['tail_fraction'] for r in rs),needs_review=sum(r['asr']['needs_review'] for r in rs),total_request_seconds=sum(r['request_seconds'] for r in rs),total_audio_seconds=sum(r['audio_seconds'] for r in rs))
    save(OUT/'summary.json',summary)
    from tools.report_luoxi_cuts import report
    report()
    print(json.dumps(summary),flush=True)
if __name__=='__main__': main()
