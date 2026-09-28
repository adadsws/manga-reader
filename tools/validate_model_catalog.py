# 通过正式Reader API逐个加载并试读动态目录中的全部模型；不读取漫画图片。
import html
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/model-catalog-validation-20260921'
CFG=json.loads((ROOT/'config/reader.json').read_text(encoding='utf-8'))
BASE='http://127.0.0.1:8765';HEADERS={'X-Reader-Token':CFG['token']}

def call(path,method='GET',body=None,timeout=360):
 data=None if body is None else json.dumps(body,ensure_ascii=False).encode('utf-8')
 headers=dict(HEADERS)
 if body is not None:headers['Content-Type']='application/json; charset=utf-8'
 request=urllib.request.Request(BASE+path,data=data,headers=headers,method=method)
 try:
  with urllib.request.urlopen(request,timeout=timeout) as response:return response.status,response.headers,response.read()
 except urllib.error.HTTPError as error:return error.code,error.headers,error.read()

def read_json(path,default):
 try:return json.loads(path.read_text(encoding='utf-8'))
 except (OSError,ValueError):return default

def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def report(models,results,final):
 by_id={x['id']:x for x in results};rows=[]
 for model in models:
  result=by_id.get(model['id'],{});error=result.get('error') or '；'.join(model.get('errors',[]))
  rows.append('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{:.3f}</td><td>{}</td><td>{}</td></tr>'.format(*map(html.escape,[model['name'],model['version'],model['checkpoint'],result.get('status',model['state'])]),float(result.get('seconds',0)),html.escape(str(result.get('avatar_status','—'))),html.escape(error)))
 counts={name:sum(x.get('status')==name for x in results) for name in ('available','failed','incomplete','skipped')}
 doc="""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>全部朗读模型动态目录验证</title><style>body{{font:15px/1.6 system-ui;margin:28px auto;max-width:1400px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:7px;vertical-align:top}}th{{background:#eef4f8}}</style><h1>全部朗读模型动态目录验证</h1><p>电脑端动态扫描 models/gpt-sovits，排除 archive/~archive。每个可选项均通过正式 /models/select 加载 GPT、SoVITS 权重并完成参考音频试读；头像只验证HTTP响应，不显示内容。未读取漫画图片。</p><p><b>结果：</b>可用 {available}，失败 {failed}，不完整 {incomplete}，跳过 {skipped}；测试后恢复初始模型：{restored}。</p><table><tr><th>名称</th><th>型号</th><th>检查点</th><th>结果</th><th>加载+试读</th><th>头像HTTP</th><th>错误</th></tr>{rows}</table><p><a href='results.json'>逐项JSON</a> · <a href='catalog.json'>初始目录</a> · <a href='final.json'>最终状态</a></p></html>""".format(**counts,restored=html.escape(str(final.get('restored'))),rows=''.join(rows))
 (OUT/'index.html').write_text(doc,encoding='utf-8')

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 status,_,raw=call('/models');assert status==200,(status,raw[:200])
 catalog=json.loads(raw);models=catalog['models'];initial=catalog['active_id']
 if not (OUT/'catalog.json').exists():save(OUT/'catalog.json',catalog)
 valid_ids={x['id'] for x in models};previous=[x for x in read_json(OUT/'results.json',[]) if x['id'] in valid_ids];done={x['id']:x for x in previous};results=list(previous);save(OUT/'results.json',results)
 try:
  for number,model in enumerate(models,1):
   if model['id'] in done:
    print('SKIP',number,'/',len(models),model['name'],flush=True);continue
   record={'id':model['id'],'name':model['name'],'version':model['version'],'checkpoint':model['checkpoint'],'catalog_state':model['state']}
   if not model['selectable']:
    record.update(status='incomplete',seconds=0,error='；'.join(model.get('errors',[])))
   else:
    avatar_status,avatar_headers,avatar=call(model['avatar_url']) if model.get('avatar_url') else (0,{},b'')
    record.update(avatar_status=avatar_status,avatar_content_type=avatar_headers.get('Content-Type'),avatar_bytes=len(avatar))
    started=time.perf_counter();select_status,_,selected=call('/models/select','POST',{'model_id':model['id']});record['seconds']=round(time.perf_counter()-started,3);record['http_status']=select_status
    try:payload=json.loads(selected)
    except ValueError:payload={'detail':selected.decode('utf-8',errors='replace')}
    if select_status==200:record.update(status='available',selected=payload.get('selected'))
    else:record.update(status='failed',error=payload.get('detail',str(payload)))
   results.append(record);done[model['id']]=record;save(OUT/'results.json',results)
   print('DONE',number,'/',len(models),record['status'],model['name'],model['version'],model['checkpoint'],record['seconds'],flush=True)
 finally:
  restore_status,_,restore_raw=call('/models/select','POST',{'model_id':initial}) if initial else (0,{},b'')
  final_status,_,final_raw=call('/models')
  final={'initial_model_id':initial,'restore_http_status':restore_status,'restored':restore_status==200,'catalog':json.loads(final_raw) if final_status==200 else {'http_status':final_status}}
  save(OUT/'final.json',final);report(models,results,final)
 print('REPORT',OUT/'index.html',flush=True)
if __name__=='__main__':main()
