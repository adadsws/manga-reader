# 报告只显示模型请求文字与切分，不展示OCR区域坐标。
import hashlib,html,json,re
from pathlib import Path
from server.core import normalize_speech_text
ROOT=Path(__file__).resolve().parents[1]
def render_tts_inputs(folder,method):
    page=json.loads((folder/'ocr.json').read_text(encoding='utf-8'));items=[]
    for i,unit in enumerate(page['sentences']):
        trace=ROOT/'~outputs-intermediate/pages'/page['page_id']/(str(i+1).zfill(3)+'.tts.json')
        audio=folder/'units'/str(i+1).zfill(3)/'audio.wav'
        if trace.exists():
            records=json.loads(trace.read_text(encoding='utf-8'))
            assert len(records)==1, '当前报告要求每单元一个原生TTS请求'
            assert records[0]['audio_sha256']==hashlib.sha256(audio.read_bytes()).hexdigest()
            text=normalize_speech_text(records[0]['text']);source='saved_request_audit'
        else:
            # 没有请求审计时明确记录来源，不冒充留存的请求。
            text=normalize_speech_text(normalize_speech_text(unit['text']));source='reconstructed_from_response_and_normalization'
        items.append(dict(request=i+1,panel_id=unit.get('panel_id'),text=text,text_split_method=method,source=source))
    (folder/'tts-input.json').write_text(json.dumps(items,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    cards=[]
    for item in items:
        panel='未识别板块' if item['panel_id'] is None else '板块 '+str(item['panel_id']+1)
        cards.append('<section style="margin:12px 0;padding:12px;background:#f4f6fa;border-left:3px solid #5378b8"><b>第 '+str(item['request'])+' 段 · '+panel+'</b><div style="white-space:pre-wrap">'+html.escape(item['text'])+'</div></section>')
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+folder.name+' · TTS输入与切分</title><style>body{font:16px/1.8 system-ui;max-width:850px;margin:32px auto;padding:0 20px}</style><h1>'+folder.name+' · TTS输入与切分</h1><p>按请求顺序排列，每个框是一次TTS输入；每段使用 '+html.escape(method)+'。</p>'+''.join(cards)+'</html>'
    (folder/'tts-input.html').write_text(document,encoding='utf-8')
    return '<a href="'+folder.name+'/tts-input.html">TTS输入文字与切分（'+str(len(items))+'段）</a>'


def update_report(out):
    out=Path(out);health=json.loads((out/'health.json').read_text(encoding='utf-8'));method=health['speech_policy']['text_split_method'];p=out/'index.html';s=p.read_text(encoding='utf-8')
    for folder in sorted(out.iterdir()):
        if folder.is_dir() and (folder/'ocr.json').exists():
            old='<a href="'+folder.name+'/ocr.json">区域、文字及顺序</a>'
            link=render_tts_inputs(folder,method)
            if old in s:
                s=s.replace(old,link)
            elif '<details><summary>TTS输入文字与切分' in s:
                s=re.sub(r'<details><summary>TTS输入文字与切分.*?</details>',lambda m:link,s,count=1,flags=re.S)
            else:
                pattern=r'<a href="'+re.escape(folder.name)+r'/tts-input.html">.*?</a>'
                s=re.sub(pattern,lambda m:link,s,flags=re.S)
    p.write_text(s,encoding='utf-8')
if __name__=='__main__':
    import sys
    update_report(sys.argv[1])
