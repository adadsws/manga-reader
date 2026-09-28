# 对朗读单元进行返回前覆盖检查；复用本地Paraformer，不改模型权重或外部阅读单元。
import difflib, hashlib, io, json, re, threading, time, wave
from pathlib import Path
from pypinyin import Style, lazy_pinyin
from .debug import event
ROOT=Path(__file__).resolve().parents[1]
_lock=threading.Lock()
_model=None
_model_device=None

def asr_status():
    requested=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8')).get('asr_device','cuda')
    return dict(requested_device=requested,device=_model_device or 'not_loaded',failure_policy='strict_no_device_fallback')

def _load_model(device):
    from funasr import AutoModel
    path=json.loads((ROOT/'config/asr-validation.lock.json').read_text(encoding='utf-8'))['model_path']
    return AutoModel(model=path,device=device,disable_update=True,ncpu=4,disable_pbar=True)

def recovery_parts(text):
    """有省略号时优先保留其后的连续语句；否则按明确句末符号细分。"""
    pattern = r'.+?(?:\.{3}|$)' if '...' in text else r'.+?(?:[。！？!?；;]+|$)'
    parts = re.findall(pattern, text, flags=re.S)
    # 省略号后的句末符号不能成为独立 TTS 请求；并回上一段且保持原文无损。
    merged=[]
    for part in parts:
        if merged and not re.search(r'[\u3400-\u9fffA-Za-z0-9]',part):
            merged[-1]+=part
        else:
            merged.append(part)
    parts=merged
    return parts if len(parts) > 1 and ''.join(parts) == text else [text]

def coverage(expected, actual):
    def chars(s):
        return ''.join(re.findall(r'[\u3400-\u9fff0-9]',s)).replace('恩','嗯')
    def tokens(s):
        # Paraformer常把同音字和句末喘声写成别字或省略；用现有拼音库检查语义骨架。
        content=''.join(ch for ch in chars(s) if ch not in '啊哈呀哎哦呃嗯唔诶')
        result=[]
        for value in lazy_pinyin(content,errors='default'):
            result.append(re.sub(r'ng$', 'n', value))
        return result
    def onset_tokens(s):
        content=''.join(ch for ch in chars(s) if ch not in '啊哈呀哎哦呃嗯唔诶')
        full=lazy_pinyin(content,errors='default')
        initials=lazy_pinyin(content,style=Style.INITIALS,strict=False,errors='default')
        return [initial or syllable[:1] for initial,syllable in zip(initials,full)]
    def metrics(target,heard):
        matched=set();matched_heard=set()
        for block in difflib.SequenceMatcher(None,target,heard,autojunk=False).get_matching_blocks():
            matched.update(range(block.a,block.a+block.size))
            matched_heard.update(range(block.b,block.b+block.size))
        size=len(target);edge=min(4,size)
        return (
            len(matched)/max(1,size),
            sum(i in matched for i in range(edge))/max(1,edge),
            sum(i in matched for i in range(size-edge,size))/max(1,edge),
            len(matched_heard)/max(1,len(heard)),
        )
    target_chars,heard_chars=chars(expected),chars(actual)
    alignment,head,tail,precision=metrics(target_chars,heard_chars)
    target,heard=tokens(expected),tokens(actual)
    phonetic_alignment,phonetic_head,phonetic_tail,phonetic_precision=metrics(target,heard)
    onset_target,onset_heard=onset_tokens(expected),onset_tokens(actual)
    onset_alignment,onset_head,onset_tail,onset_precision=metrics(onset_target,onset_heard)
    if target:
        # 覆盖率防漏读，精确率防参考提示词或其他无关语句混入成品。
        syllables_ok=(phonetic_alignment>=.75 and phonetic_head>=.75 and
                      phonetic_tail>=.75 and phonetic_precision>=.7)
        onsets_ok=(onset_alignment>=.75 and onset_head>=.75 and
                   onset_tail>=.75 and onset_precision>=.7)
        passed=syllables_ok or onsets_ok
    else:
        # 纯语气词无法逐字可靠转写；至少要求ASR检出人声，避免接受静音。
        passed=bool(heard_chars)
    return dict(text=actual,alignment=alignment,head=head,tail=tail,precision=precision,
                phonetic_alignment=phonetic_alignment,phonetic_head=phonetic_head,
                phonetic_tail=phonetic_tail,phonetic_precision=phonetic_precision,
                onset_alignment=onset_alignment,onset_head=onset_head,
                onset_tail=onset_tail,onset_precision=onset_precision,passed=passed)

def recognize(body):
    global _model,_model_device
    with _lock:
        if _model is None:
            event('tts_coverage_model_loading')
            requested=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8')).get('asr_device','cuda')
            # ASR 设备故障必须显式失败，不能把 CUDA 异常隐藏成 CPU 慢路径。
            _model=_load_model(requested);_model_device=requested
            event('tts_coverage_model_ready')
        path=ROOT/'~temp/tts-coverage/current.wav';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        result=_model.generate(input=str(path),disable_pbar=True)
        return ''.join(x.get('text','') for x in result)

def join_audio(parts):
    frames=[];fmt=None
    for body in parts:
        with wave.open(io.BytesIO(body)) as w:
            current=(w.getnchannels(),w.getsampwidth(),w.getframerate())
            if fmt is not None and current!=fmt:raise ValueError('保护合成音频格式不一致')
            fmt=current;pcm=w.readframes(w.getnframes())
            if len(pcm)!=w.getnframes()*fmt[0]*fmt[1]:raise ValueError('保护合成音频不完整')
            frames.append(pcm)
    output=io.BytesIO()
    with wave.open(output,'wb') as w:w.setparams((*fmt,0,'NONE','not compressed'));w.writeframes(b''.join(frames))
    return output.getvalue()

def guarded_synthesize(text,synth,base_seed=42,cancelled=None):
    attempts=[]
    def check_cancelled():
        if cancelled and cancelled():raise ValueError('页面已取消')
    def attempt(part,seed):
        check_cancelled();started=time.monotonic();synth_started=time.monotonic()
        body=synth(part,seed);synthesis_seconds=time.monotonic()-synth_started;check_cancelled()
        asr_started=time.monotonic();recognized=recognize(body);asr_seconds=time.monotonic()-asr_started
        check=coverage(part,recognized);check_cancelled()
        record=dict(text=part,seed=seed,audio_sha256=hashlib.sha256(body).hexdigest(),coverage=check,
                    seconds=round(time.monotonic()-started,3),
                    synthesis_seconds=round(synthesis_seconds,3),asr_seconds=round(asr_seconds,3))
        attempts.append(record);event('tts_coverage_checked',passed=check['passed'],alignment=round(check['alignment'],3),attempt=len(attempts))
        return body,record
    original,record=attempt(text,base_seed)
    original_record=record
    if record['coverage']['passed']:return original,dict(requests=[record],attempts=attempts,recovered=False,verified=True)
    # 仅在确有覆盖缺失时内部细分；单句则只换种子重试，不改外部播放单元。
    parts=recovery_parts(text)
    seeds=list(dict.fromkeys((base_seed,0,7)));accepted=[];bodies=[];unverified=[]
    event('tts_recovery_start',parts=len(parts))
    for part in parts:
        part_seeds=seeds if len(parts)>1 else seeds[1:]
        first_body=first_record=None
        for seed in part_seeds:
            body,record=attempt(part,seed)
            if first_body is None:first_body,first_record=body,record
            if record['coverage']['passed']:
                bodies.append(body);accepted.append(record);break
        else:
            # 极短拟声可能始终无法被 ASR 确认。多段修复中保留已经通过的语义片段，
            # 只让失败片段回退到自身首个完整 WAV，避免整单元退回已知漏读的原音频。
            bodies.append(first_body);accepted.append(first_record);unverified.append(first_record)
    if unverified:
        check_cancelled();event('tts_coverage_unverified',attempts=len(attempts))
        if len(unverified)==len(parts):
            return original,dict(requests=[original_record],attempts=attempts,recovered=False,
                                 verified=False,fallback='initial_complete_wav')
        event('tts_recovery_partial',requests=len(accepted),unverified=len(unverified),attempts=len(attempts))
        return join_audio(bodies),dict(requests=accepted,attempts=attempts,recovered=True,verified=False,
                                       partial_recovery=True,unverified_parts=len(unverified),
                                       fallback='per_part_complete_wav')
    check_cancelled();event('tts_recovery_complete',requests=len(accepted),attempts=len(attempts))
    return join_audio(bodies),dict(requests=accepted,attempts=attempts,recovered=True,verified=True)
