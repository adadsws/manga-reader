"""复放已有 OCR 坐标的全册回归；不显示或读取图片内容。"""
import argparse, json, re
from pathlib import Path
from server.core import filter_speech_candidates, group_lines, compact_reading_order, map_text_to_panels, merge_page, normalize_speech_text, speech_chunks
from server.ocr import Region, sort
ROOT=Path(__file__).resolve().parents[2]
def validate(out):
    data=json.loads((out/'layout-diagnostics.json').read_text(encoding='utf-8'))
    summary=[];dest=out/'offline';dest.mkdir(exist_ok=True)
    for name,f in data.items():
        kept,excluded=filter_speech_candidates(f['rows'],f['text_regions'])
        groups=group_lines(kept);mapping=map_text_to_panels([g['box'] for g in groups],f['panels']);panels={}
        for g,p in zip(groups,mapping):panels.setdefault(p,[]).append(g)
        ordered=[]
        for p,rows in sorted(panels.items()):
            ids=compact_reading_order(rows)
            ordered.extend([rows[i] for i in ids] if ids is not None else [r.data for r in sort._simple_sort([Region(g) for g in rows],True)])
        sentences=[dict(g,text=normalize_speech_text(g['original']),confidence=g['score']) for g in ordered]
        if sentences:sentences[0]['excluded_texts']=excluded
        units=merge_page(sentences)
        (dest/(name+'.json')).write_text(json.dumps(units,ensure_ascii=False,indent=2),encoding='utf-8')
        text=''.join(u['text'] for u in units);chunks=speech_chunks(text)
        assert ''.join(chunks)==normalize_speech_text(text)
        assert not re.search('[A-Za-zぁ-ヺ]',text)
        summary.append({'source':name,'raw_rows':len(f['rows']),'kept_rows':len(kept),'excluded_rows':len(excluded),'speech_characters':len(text),'chunks':len(chunks),'max_chunk':max(map(len,chunks),default=0),'english_or_kana_in_speech':False})
    (out/'offline-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('offline pages',len(summary),'empty pages',sum(not x['speech_characters'] for x in summary),'excluded rows',sum(x['excluded_rows'] for x in summary))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=ROOT/'~outputs-intermediate/evidence/reading-feedback-20260920');args=parser.parse_args();validate(args.out)
