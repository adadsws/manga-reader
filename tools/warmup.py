# 启动预热客户端：后台发送一次请求，前台按结构化事件实时输出，不读取正文或图片。
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from live_debug import JsonTail, emit

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'))
result={}

def request_warmup():
    request=urllib.request.Request(
        'http://127.0.0.1:8765/warmup',
        data=b'',
        headers={'X-Reader-Token':CFG['token']},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request,timeout=360) as response:
            result['value']=json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            detail=json.loads(error.read()).get('detail')
        except Exception:
            detail=None
        result['error']=detail or f'HTTP {error.code}'
    except Exception as error:
        result['error']=str(error)

def main():
    tail=JsonTail(ROOT/'~temp/logs/debug.jsonl',history=False)
    worker=threading.Thread(target=request_warmup)
    worker.start()
    while worker.is_alive():
        for text in tail.read():emit('电脑',text)
        worker.join(.05)
    for text in tail.read():emit('电脑',text)
    if result.get('error'):
        print('启动预热失败：'+result['error'],file=sys.stderr,flush=True)
        return 1
    value=result['value']
    if value.get('cached'):
        print('电脑服务已处于热状态，无需重复预热。',flush=True)
    else:
        steps=value.get('steps',{})
        print(f"OCR预热完成：{float(steps.get('ocr_seconds',0)):.3f}秒",flush=True)
        print(f"TTS预热完成：{float(steps.get('tts_seconds',0)):.3f}秒",flush=True)
        print(f"ASR预热完成：{float(steps.get('asr_seconds',0)):.3f}秒",flush=True)
        print(f"服务已就绪且已预热，总计：{float(value.get('seconds',0)):.3f}秒",flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
