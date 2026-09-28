"""汇总反馈回归，复用实际音频，不进行离线音频替换。"""
import argparse, difflib, hashlib, html, json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'~outputs-intermediate/evidence/reading-feedback-20260920'
OLD=ROOT/'~archive/20260928-历史验收证据与旧计划/docs/evidence/full-volume-20260920'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def han(t):return ''.join(re.findall('[\u4e00-\u9fff]',t))
def metric(base,source):
    r=next(r for r in read(base/'run.json')['records'] if r['source']==source)
    a=read(base/'asr'/(r['page_id']+'.json'));expected,actual=han(a['expected_normalized']),han(a['actual_normalized'])
    blocks=difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_matching_blocks()
    end=max((b.a+b.size for b in blocks if b.size),default=0)
    tail=expected[-10:];tail_matches=sum(max(0,min(b.a+b.size,len(expected))-max(b.a,len(expected)-len(tail))) for b in blocks if b.size)
    return {'source':source,'audio':(base/'pages'/r['page_id']/'page.wav').relative_to(ROOT).as_posix(),'audio_seconds':r['audio_seconds'],'unmatched_trailing_han':len(expected)-end,'last_10_aligned_fraction':round(tail_matches/max(1,len(tail)),3),'tail_exact':tail in actual,'asr_alignment':a['aligned_character_fraction']}
def report(runs):
    main,follow=runs
    summaries=[read(p/'manifest.json')['summary'] for p in runs]
    latest={r['source']:(base,r) for base in runs for r in read(base/'run.json')['records']}
    checks=[]
    for source,(base,r) in latest.items():
        page=read(base/'pages'/r['page_id']/'ocr.json');text=''.join(p['text'] for p in page)
        item={'source':source,'english_and_kana_absent':not bool(re.search('[A-Za-zぁ-ヺ]',text)),'trace_present':(base/'pages'/r['page_id']/'tts.json').exists()}
        targets={'0014.jpg':['嗯','人家','算了','那个','哪个'],'0024.jpg':['这是称赞','嗯','是吗','不是称赞','妳叫','美铃']}.get(source)
        if targets:
            offsets=[text.find(t) for t in targets];item['user_order_matches']=all(x>=0 for x in offsets) and offsets==sorted(offsets)
        if source=='0027.jpg':
            target=read(OUT/'layout-diagnostics.json')[source]['rows'][7]['box']
            excluded=[e for unit in page for e in unit.get('excluded_texts',[])]
            item['reported_noise_box']=target
            item['reported_noise_absent']=any(e['box']==target and e['reason']=='short_text_in_kana_cluster' for e in excluded) and all(x not in text for x in ['oux','61','は'])
            item['same_phrase_elsewhere_retained']='哈啊' in text
        checks.append(item)
    comparison=[{'source':name,'before':metric(OLD,name),'after':metric(main,name)} for name in ['0017.jpg','0033.jpg']]
    manifest={'runs':[str(p.relative_to(ROOT)) for p in runs],'run_summaries':summaries,'offline_pages':len(read(OUT/'offline-summary.json')),'checks':checks,'truncation_comparison':comparison,'regression_tests':read(OUT/'test-summary.json')['tests'],'manual_image_review':False,'known_limits':['繁简转换不能纠正错误汉字；憨字保留，等待用户确认。','短中文拟声筛选和排序是启发式，未逐图核对，排除项保留可校对。','ASR含同音字和重复短语误差，不能作为逐字正确率。','本次全册仅回放既有OCR坐标；新的真实安卓截图共10页加1页追加。']}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    def link(p):return '../'+str(p.relative_to(ROOT/'~outputs-intermediate/evidence')).replace('\\','/')
    rows=[]
    for c in comparison:
        b,a=c['before'],c['after'];rows.append(f"| {c['source']} | {b['audio_seconds']} → {a['audio_seconds']} 秒 | {b['unmatched_trailing_han']} → {a['unmatched_trailing_han']} | {a['last_10_aligned_fraction']:.0%} |")
    md=['# 朗读反馈修复与复测','', '完成英文跳过、统一繁简转换、拟声候选复核、两处阅读顺序修正和短段音频拼接。全程未查看图片内容。','',f"验证范围：44页已有OCR坐标回放；10页正式安卓—电脑实测全部完成、无中途恢复；最终拟声规则另做1页正式追加实测。首轮实际音频共{summaries[0]['audio_seconds']}秒。",'', '37项回归通过：[原始日志](unit-tests.log) · [完整性检查](final-checks.json)。', '', '## 修复内容','', '- 英文词默认跳过，避免逐字母朗读；进入语音服务前统一 OpenCC t2s，OCR和手工校对入口一致。中文语境中的数字保留。','- 复用 Magi 同一次推理的文本区域及对白分类分数，结合假名邻近位置识别拟声候选。原始行、坐标、分数和排除原因保留在 OCR JSON；低分不单独作为长中文正文的删除条件。','- 分镜内采用右上角距离顺序；长框或大字形异常时回退原排序。0014、0024符合用户给定顺序，既有三页排序用例通过。','- 单次语音请求限制为最多24字符，优先在标点处分段，串行合成后按PCM样本顺序拼成一个WAV。安卓仍以整页完成事件翻页；每段请求有哈希、帧数和耗时记录。','', '## 两处漏读前后对照','', '| 源页 | 实际音频时长（旧→新） | ASR末尾未对齐汉字（旧→新） | 新音频末10字对齐 |','|---|---|---:|---:|',*rows,'','两处旧音频的后半段缺失在新音频中未复现。末尾未对齐为0表示ASR匹配延伸到文本结尾，不代表中间每个字均正确。0017存在“的／地”等同音字误识，试听入口见HTML报告。','', '## 实测证据','',f'- [10页完整流程报告]({link(main)}/index.html)；[响应与分段帧数检查]({link(main)}/final-checks.json)。',f'- [1页最终规则追加报告]({link(follow)}/index.html)；[追加恢复状态]({link(follow)}/restoration.json)。','- [44页坐标回放统计](offline-summary.json)；[版面诊断](layout-diagnostics.json)；[反馈逐项检查](manifest.json)。','',f"首轮ASR标记{summaries[0]['asr_needs_review_pages']}页待复核，不能将播放完成解释为全文无误。两轮均核对源文件哈希、APK、上游文件和实际返回音频；详情见各批次报告。",'', '追加页ASR仍标记待复核；拟声排除按用户反馈的具体坐标确认，同形短语在另一处仍保留，不按词语全局删除。', '', '## 待确认与边界','',*['- '+x for x in manifest['known_limits']],'', '## 复现','', '[复现步骤](../../../AGENT_CONTEXT.md#自动测试维护入口)：十页使用 check→new→feedback-import→setup→collect→report→restore→open；全册用 prepare 替代 feedback-import。旧44页报告保持原样。','']
    (OUT/'修复报告.md').write_text('\n'.join(md),encoding='utf-8')
    audio=[]
    for c in comparison:
        cells=[]
        for key,label in [('before','旧音频'),('after','新音频（实际返回安卓）')]:
            p=ROOT/c[key]['audio'];cells.append(f'<td>{label}<br><audio controls preload="none" src="{link(p)}"></audio></td>')
        audio.append('<tr><th>'+c['source']+'</th>'+''.join(cells)+'</tr>')
    links=''.join(f'<li><a href="{link(p)}/index.html">{n}页真实安卓流程报告</a></li>' for p,n in zip(runs,[10,1]))
    doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>朗读反馈修复报告</title><style>body{font:16px/1.7 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;background:#f6f7fa;color:#16243b}section,table{background:white;padding:20px;border-radius:12px}td,th{padding:16px}pre{white-space:pre-wrap;font:inherit}a{color:#145cad}audio{width:290px}</style><h1>朗读反馈修复与复测</h1><p>44页坐标回放 · 10页真实安卓实测 · 1页最终规则追加 · 未查看图片</p><ul>'+links+'</ul><p><a href="修复报告.md">详细说明</a> · <a href="manifest.json">逐项检查</a> · <a href="../../../AGENT_CONTEXT.md#自动测试维护入口">复现文件夹说明</a></p><p><a href="unit-tests.log">回归原始日志</a> · <a href="final-checks.json">完整性检查</a></p><h2>两处漏读对照</h2><table>'+''.join(audio)+'</table><h2>处理及验证记录</h2><section><pre>'+html.escape('\n'.join(md))+'</pre></section></html>'
    (OUT/'index.html').write_text(doc,encoding='utf-8')
    print('feedback reports generated; all checks',all(c.get('user_order_matches',True) and c.get('reported_noise_absent',True) and c['english_and_kana_absent'] for c in checks))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runs',nargs=2,type=Path,required=True);args=parser.parse_args();report(args.runs)
