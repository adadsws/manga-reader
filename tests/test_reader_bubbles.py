# 气泡边界、连接区域及正式接口的行为回归。
import unittest,json
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from server.core import group_lines,prepare_bubbles,prepare_panels,prepare_panel_sentences,filter_speech_candidates,map_text_to_panels,compact_reading_order,normalize_speech_text
from server.app import app,settings,store
class BubbleTests(unittest.TestCase):
    def row(self,box,text):return dict(box=box,text=text,score=.9)
    def test_touching_regions_do_not_merge(self):
        rows=[self.row([100,10,120,90],'右？'),self.row([75,10,95,90],'左！')]
        self.assertEqual(len(group_lines(rows)),1)
        regions=[{'box':[98,0,130,100]},{'box':[65,0,97,100]}]
        self.assertEqual(len(group_lines(rows,regions)),2)
    def test_connected_shared_region_keeps_local_blocks(self):
        rows=[self.row([100,10,120,80],'上右？'),self.row([75,10,95,80],'上左！'),self.row([140,160,160,230],'下右？'),self.row([115,160,135,230],'下左！')]
        groups=group_lines(rows,[{'box':[65,0,170,240]}])
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['original'],'上右？上左！下右？下左！')
        self.assertEqual(len(groups[0]['columns']),4)
    def test_shared_detection_never_bridges_panels(self):
        rows=[self.row([100,10,120,90],'右'),self.row([75,10,95,90],'左')]
        self.assertEqual(len(group_lines(rows,[{'box':[60,0,130,100]}],[[98,0,130,100],[60,0,97,100]])),2)
    def test_punctuation_and_original_preserved(self):
        rows=[dict(text='嗯？是什麼？人家！',original='嗯？是什麼？人家！',box=[0,0,20,40]),dict(text='算了……那個？',original='算了……那個？',box=[30,0,50,40])]
        units=prepare_bubbles(rows,'luoxi')
        self.assertEqual([r['text'] for r in units],['嗯？是什么？人家！','算了...那个？'])
        self.assertEqual(units[0]['original'],rows[0]['original'])
    def test_pages_preserves_two_audio_units(self):
        class OCR:
            def read(self,body):return [dict(text='第一组？继续说！',original='第一组？继续说！',box=[1,2,3,4]),dict(text='第二组。',original='第二组。',box=[5,6,7,8])]
        client=TestClient(app);headers={'X-Reader-Token':settings['token'],'X-Reader-History-Reuse':'off'}
        with patch('server.app.ocr',OCR()),patch.dict(settings,{'save_debug_pages':False}):
            page=client.post('/pages',headers=headers,content=b'fixture').json()
        self.assertEqual(len(page['sentences']),2)
        self.assertEqual(page['sentences'][0]['text'],'第一组？继续说！')
        with patch('server.app.synthesize',return_value=b'audio') as synth:
            for i in range(2):self.assertEqual(client.get('/pages/'+page['page_id']+'/audio/'+str(i),headers=headers).content,b'audio')
            self.assertEqual([c.args[0] for c in synth.call_args_list],[r['text'] for r in page['sentences']])
        store.cancel(page['page_id'])

    def test_user_connected_bubble_coordinate_fixture(self):
        f=json.loads((Path(__file__).parent/'fixtures/reading_feedback.json').read_text(encoding='utf-8'))['0014.jpg']
        groups=group_lines(filter_speech_candidates(f['rows'],f['text_regions'])[0],f['text_regions'],f['panels'])
        mapping=map_text_to_panels([r['box'] for r in groups],f['panels'])
        chosen=[r for r,p in zip(groups,mapping) if p==max(mapping)]
        order=compact_reading_order(chosen)
        self.assertIsNotNone(order)
        text=normalize_speech_text(''.join(chosen[i]['original'] for i in order))
        offsets=[text.index(w) for w in ('嗯','人家','算了','那个','哪个')]
        self.assertEqual(offsets,sorted(offsets))

    def test_panel_merge_preserves_bubble_order_and_boundaries(self):
        rows=[dict(text=t,original=t,panel_id=p,panel_box=[p*100,0,p*100+90,90],box=[1,2,3,4]) for p,t in [(0,'嗯？是什么？'),(0,'人家？'),(0,'算了……那个？'),(1,'下一格！')]]
        result=prepare_panels(rows,'luoxi')
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['text'],'嗯？是什么？人家？算了...那个？')
        self.assertEqual(result[0]['bubble_count'],3)
        self.assertEqual([x['text'] for x in result[0]['segments']],[normalize_speech_text(r['text']) for r in rows[:3]])
        self.assertEqual([x['original'] for x in result[0]['segments']],[r['original'] for r in rows[:3]])
        self.assertEqual(result[1]['panel_id'],1)
    def test_unknown_panel_keeps_bubble_fallback(self):
        result=prepare_panels([dict(text='第一组！'),dict(text='第二组？')],'luoxi')
        self.assertEqual(len(result),2)
        self.assertTrue(all(x['mode']=='bubble' for x in result))

    def test_panel_then_period_preserves_other_punctuation(self):
        rows=[dict(text='第一句！继续，别拆？。第二句……',original='第一句！继续，别拆？。第二句……',panel_id=0,panel_box=[0,0,100,100]),dict(text='另一个板块',original='另一个板块',panel_id=1,panel_box=[100,0,200,100])]
        units=prepare_panel_sentences(rows,'luoxi')
        self.assertEqual([u['text'] for u in units],['第一句！继续，别拆？。','第二句...','另一个板块。'])
        self.assertEqual([u['panel_id'] for u in units],[0,0,1])
        self.assertEqual(units[1]['sentence_index'],1)
    def test_ideographic_comma_does_not_create_sentence_boundary(self):
        rows=[dict(text='第一部分、',original='第一部分、',panel_id=0,panel_box=[0,0,100,100]),dict(text='第二部分。',original='第二部分。',panel_id=0,panel_box=[0,0,100,100])]
        units=prepare_panel_sentences(rows,'luoxi')
        self.assertEqual([u['text'] for u in units],['第一部分、第二部分。'])
        self.assertEqual(units[0]['bubble_count'],2)
    def test_actual_feedback_column_order_and_commas(self):
        f=json.loads((Path(__file__).parent/'fixtures/panel_period_feedback.json').read_text(encoding='utf-8'))
        groups=group_lines(f['columns'],panels=[f['panel_box']])
        order=compact_reading_order(groups)
        rows=[dict(g,text=g['original'],panel_id=0,panel_box=f['panel_box']) for g in (groups[i] for i in order)]
        units=prepare_panel_sentences(rows,'luoxi')
        self.assertEqual([u['text'] for u in units],['哈！刚才！对舌头做了什么。','就算说被袭击的是我也没人相信吧...'])
