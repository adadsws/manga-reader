# 三页原图经正式电脑接口逐气泡合成；不查看图片。
import argparse,hashlib,html,io,json,time,wave
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'~outputs-intermediate/evidence/bubble-units-20260920'
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
 global OUT
 parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(OUT));parser.add_argument('--unit',choices=('bubble','panel','panel_sentence'),default='bubble');args=parser.parse_args();OUT=Path(args.out);OUT.mkdir(parents=True,exist_ok=True)
 if (OUT/"summary.json").exists(): raise RuntimeError("已有验证证据，请先归档或指定新的OUT目录")
 cfg=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'));headers={'X-Reader-Token':cfg['token']};url='http://127.0.0.1:8765';summary=[];rows=[]
 save(OUT/'health.json',requests.get(url+'/health',timeout=20).json())
 for path in sorted((ROOT/'secrets/manga').glob('*.jpg')):
  folder=OUT/path.stem;folder.mkdir(exist_ok=True);t=time.perf_counter()
  r=requests.post(url+'/pages',data=path.read_bytes(),headers=headers,timeout=300);r.raise_for_status();page=r.json();save(folder/'ocr.json',page)
  ocr_seconds=time.perf_counter()-t;frames=[];fmt=None;units=[]
  for i,unit in enumerate(page['sentences']):
   target=folder/'units'/str(i+1).zfill(3);target.mkdir(parents=True,exist_ok=True)
   t=time.perf_counter();r=requests.get(url+'/pages/'+page['page_id']+'/audio/'+str(i),headers=headers,timeout=240);r.raise_for_status();elapsed=time.perf_counter()-t
   (target/'audio.wav').write_bytes(r.content)
   with wave.open(io.BytesIO(r.content)) as w:
    current=(w.getnchannels(),w.getsampwidth(),w.getframerate());body=w.readframes(w.getnframes());seconds=w.getnframes()/w.getframerate()
    assert w.getnframes()>0 and len(body)==w.getnframes()*w.getnchannels()*w.getsampwidth()
    assert fmt is None or fmt==current;fmt=current;frames.append(body)
   record=dict(index=i,characters=len(unit['text']),box=unit['box'],group_id=unit['group_id'],audio_seconds=seconds,request_seconds=elapsed,audio_sha256=hashlib.sha256(r.content).hexdigest());save(target/'metadata.json',record);units.append(record)
   print('AUDIO',path.name,i+1,'/',len(page['sentences']),flush=True)
  with wave.open(str(folder/'sequence.wav'),'wb') as w:
   w.setnchannels(fmt[0]);w.setsampwidth(fmt[1]);w.setframerate(fmt[2]);w.writeframes(b''.join(frames))
  requests.delete(url+'/pages/'+page['page_id'],headers=headers,timeout=20).raise_for_status()
  item=dict(source=path.name,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),units=len(units),ocr_seconds=ocr_seconds,audio_seconds=sum(x['audio_seconds'] for x in units),synthesis_seconds=sum(x['request_seconds'] for x in units),records=units);summary.append(item);save(OUT/'summary.json',summary)
  rows.append(f'<tr><td>{path.name}</td><td>{len(units)}</td><td>{item["audio_seconds"]:.2f}s</td><td><audio controls preload="none" src="{path.stem}/sequence.wav"></audio><br><a href="{path.stem}/ocr.json">区域、文字及顺序</a></td></tr>')
 (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>逐气泡朗读验证</title><style>body{font:16px/1.8 system-ui;margin:32px}td,th{padding:12px;border:1px solid #ddd}table{border-collapse:collapse}</style><h1>三页逐气泡朗读验证</h1><p>文字区域 → 区域内完整文本 → 排序 → 每个单元使用洛茜V2ProPlus/cut0。取消整页合并及区域内按标点拆句。没有查看图片，没有安卓实测。模型文字区域不是精确气泡轮廓，连接关系及阅读顺序仍可能识别错误。</p><p>下列播放器是本轮各单元实际响应按顺序拼接的试听，未经重新合成；单元音频与元数据在各页units目录。播放完成不等于无漏字，未人工核对音质。</p><table><tr><th>页面</th><th>朗读单元</th><th>音频时长</th><th>顺序拼接试听</th></tr>'+''.join(rows)+'</table><p><a href="summary.json">请求及音频清单</a> · <a href="tests.log">回归日志</a></p>',encoding='utf-8')
 if args.unit in ('panel','panel_sentence'):
  report=OUT/'index.html';text=report.read_text(encoding='utf-8').replace('逐气泡','按分镜板块').replace('文字区域 → 区域内完整文本 → 排序 → 每个单元使用','分镜检测 → 板块内按原顺序合并气泡 → 每板块一个单元使用').replace('取消整页合并及区域内按标点拆句。','保留板块边界，不合并整页，不按标点重新拆句。');report.write_text(text,encoding='utf-8')
 if args.unit=='panel_sentence':
  report=OUT/'index.html';text=report.read_text(encoding='utf-8').replace('按分镜板块','板块后按句号').replace('每板块一个单元使用','板块内再按中文句号分段，每段使用').replace('不按标点重新拆句','仅按中文句号拆分，保留逗号、问叹号及省略号');report.write_text(text,encoding='utf-8')
 from tools.report_tts_inputs import update_report
 update_report(OUT)
 print('DONE',[(x['source'],x['units']) for x in summary],flush=True)
if __name__=='__main__':main()
