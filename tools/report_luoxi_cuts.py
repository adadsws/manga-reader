# 从现有证据重建六组试听页和说明，不触发OCR或语音合成。
import html,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'~outputs-intermediate/evidence/luoxi-cut-20260920'
def report():
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    summary=read(OUT/'summary.json'); inputs=read(OUT/'inputs.json'); names=list(summary)
    md=['# 洛茜三页六组对比','', '默认配置与采用原因见decision.json。以下均为本次三页样本的ASR覆盖筛查，不能作为发音准确率或普遍音质结论。', '', '| 组合 | 全文平均对齐 | 末20字平均对齐 | 三页合成总秒数 | 待复核 |', '|---|---:|---:|---:|---:|']
    table='<table><tr><th>组合</th><th>全文对齐</th><th>末20字对齐</th><th>三页合成耗时</th><th>待复核</th></tr>'
    for name,v in summary.items():
        md.append(f'| {name} | {v["mean_alignment"]:.1%} | {v["mean_tail_alignment"]:.1%} | {v["total_request_seconds"]:.2f} | {v["needs_review"]}/3 |')
        table+=f'<tr><td>{name}</td><td>{v["mean_alignment"]:.1%}</td><td>{v["mean_tail_alignment"]:.1%}</td><td>{v["total_request_seconds"]:.2f}s</td><td>{v["needs_review"]}/3</td></tr>'
    table+='</table>'
    limits='只使用secrets/manga目录0009.jpg、0012.jpg、0041.jpg。原图由程序处理，代理未查看图片；本轮没有安卓实测。ASR会误识同音字、重复词和短语，指标不是发音准确率，也不能证明OCR正确。待复核阈值为全文对齐低于90%或末20字低于80%。未人工评价自然度。每页每组仅一次，不能证明稳定性或外推到其他页面。'
    protocol='三页经正式电脑/pages接口识别、排序、繁简转换和现有过滤各一次，得到123、117、75字符文本。六组共享文本和同一8.42秒中文参考；不作24字预切分。V4选GPT e10 / SoVITS e16，V2组实际为V2ProPlus，选GPT e20 / SoVITS e20；没有比较其他训练轮次。固定seed42、top_k5、batch1、串行非流式、speed1、fragment_interval0.3。比较的是这两套检查点，不把差异全部归因于架构版本。'
    timing='每个模型和切法预热后计时，耗时从发出请求至读完响应，不含加载、预热、OCR或ASR。先完成cut1/cut3，按用户追加要求再补cut0，已有12份音频保留；因此cut0是在后续时段运行，性能差异可能受GPU状态影响，不能据此断言稳定速度排名。模型按V4、V2ProPlus先后运行。'
    cutnote='cut0保持整段输入，不能称作语义切分；cut1组合约四个标点片段；cut3仅按中文句号切分。JSON中的raw_cut_output是原始切法输出，后续上游还会合并不足5字片段、补标点和处理超长文本，不把原始行数当模型调用次数。'
    reproduce='启动电脑服务，在项目目录设置PYTHONPATH为~temp/ocrdeps和项目根目录，使用config/runtime.json的Python运行 -B -m tools.compare_luoxi_cuts。测试会临时切换权重，请勿同时从安卓发起朗读。已有音频经哈希/参数校验后复用；重新计时需先归档整个证据目录并更新OUT。只重建报告运行 -B -m tools.report_luoxi_cuts。模型加载接口会写tts.yaml；脚本结束恢复reader.json指定版本，随后重启电脑服务清理上游版本切换缓存。'
    md+=['','## 流程与范围','',limits,'',protocol,'',timing,'',cutnote,'','统一OCR文本 → 原生cut0/cut1/cut3 → 洛茜V4/V2ProPlus → WAV → CPU Paraformer → 对齐筛查和试听。','','## 复现','',reproduce,'','[并排试听](index.html) · [请求参数](parameters.json) · [输入与源文件哈希](inputs.json) · [V4加载证据](load-v4.json) · [V2ProPlus加载证据](load-v2ProPlus.json) · [参考音频哈希](model.json)']
    md+=['','## 电脑接口回归','','45项电脑回归通过，固定上游260文件哈希一致。APK仅构建，未安装或安卓实测。默认版本重启后，经正式电脑音频接口再次合成0009，时长和ASR全文对齐与基准一致；PCM哈希不同、相关系数0.999967，固定seed不保证逐字节确定性。该重复样本不计入18份主对比。','','[电脑接口重复验证](validation/pc-smoke.json) · [重复音频](validation/pc-smoke.wav) · [回归日志](validation/all-tests.log) · [上游完整性](validation/upstream-verification.json)']
    (OUT/'对比报告.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    rows=[]
    for c in inputs:
        cells=[]
        for name in names:
            r=read(OUT/c['id']/(name+'.json'))
            cells.append(f'<td>全文 {r["asr"]["aligned_fraction"]:.1%}；末20字 {r["asr"]["tail_fraction"]:.1%}<br>合成 {r["request_seconds"]:.2f}s / 音频 {r["audio_seconds"]:.2f}s<br><audio controls preload="none" src="{c["id"]}/{name}.wav"></audio><br><a href="{c["id"]}/{name}.json">请求、切法输出与ASR</a></td>')
        rows.append('<tr><th>'+c['id']+'</th>'+''.join(cells)+'</tr>')
    decision=read(OUT/'decision.json') if (OUT/'decision.json').exists() else {'status':'待结论'}
    intro='<p>'+html.escape(str(decision.get('explanation','待结论')))+'</p>'
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>洛茜三页六组试听</title><style>body{font:16px/1.7 system-ui;margin:28px;background:#f5f7fa;color:#17243a}td,th{padding:12px;border:1px solid #d8dce4;text-align:left}table{border-collapse:collapse;background:white}audio{width:225px}.scroll{overflow:auto}p{max-width:1100px}a{color:#185da9}</style><h1>洛茜：V4 / V2ProPlus × cut0 / cut1 / cut3</h1>'+intro+'<p>'+html.escape(limits)+'</p><p><a href="对比报告.md">完整报告与复现</a> · <a href="decision.json">采用结论</a> · <a href="inputs.json">输入哈希与OCR</a> · <a href="parameters.json">固定参数</a></p>'+table+'<h2>逐页试听</h2><p>横向滚动查看六组；播放一段会自动暂停其他音频。</p><div class="scroll"><table><tr><th>页面</th>'+''.join('<th>'+n+'</th>' for n in names)+'</tr>'+''.join(rows)+'</table></div><p>'+html.escape(timing)+'</p><script>document.addEventListener("play",e=>{document.querySelectorAll("audio").forEach(a=>{if(a!==e.target)a.pause()})},true)</script></html>'
    (OUT/'index.html').write_text(page,encoding='utf-8')
    print('Report ready: 3 pages x',len(names),'groups',flush=True)
if __name__=='__main__': report()
