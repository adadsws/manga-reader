# 动态模型目录扫描、archive排除、状态和安全切模。
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server.model_catalog import ModelCatalog

class Reply:
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):return b'{"message":"success"}'

class ModelCatalogTests(unittest.TestCase):
    def files(self,folder,name='角色',complete=True):
        folder.mkdir(parents=True)
        (folder/(name+'-e10.ckpt')).write_bytes(b'gpt')
        if complete:(folder/(name+'_e10_s10_l32.pth')).write_bytes(b'sovits')
        (folder/'【默认】测试_声音.wav').write_bytes(b'wav')
        (folder/'角色头像.webp').write_bytes(b'image')

    def test_dynamic_scan_excludes_archives_and_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'models/gpt-sovits';self.files(root/'作品-日语/v4/学校/角色')
            self.files(root/'作品-日语/v4/学校/~archive/旧角色')
            self.files(root/'作品-日语/v4/学校/损坏角色',complete=False)
            settings={'tts_url':'http://tts','voice_name':'角色','model_version':'v4','prompt_lang':'zh'}
            catalog=ModelCatalog(root,settings,Path(temp)/'state.json')
            result=catalog.scan()
            self.assertEqual(result['counts']['total'],2)
            good=next(x for x in result['models'] if x['name']=='角色')
            bad=next(x for x in result['models'] if x['name']=='损坏角色')
            self.assertEqual(good['prompt_lang'],'ja');self.assertTrue(good['selectable'])
            self.assertEqual(bad['state'],'incomplete');self.assertFalse(bad['selectable'])
            self.assertIn('缺少 SoVITS 权重（.pth）',bad['errors'])
            self.assertNotIn('旧角色',json.dumps(result,ensure_ascii=False))
            self.assertEqual(catalog.avatar(good['id']).name,'角色头像.webp')

    def test_parent_resources_multiple_checkpoints_and_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'models/gpt-sovits';voice=root/'洛茜';variant=voice/'V2ProPlus';variant.mkdir(parents=True)
            (voice/'头像.jpg').write_bytes(b'image');data=voice/'数据集';data.mkdir();ref=data/'中文参考.wav';ref.write_bytes(b'wav')
            for epoch in (15,20):(variant/f'洛茜-e{epoch}.ckpt').write_bytes(b'gpt')
            for epoch in (16,20):(variant/f'洛茜_e{epoch}_s1.pth').write_bytes(b'sovits')
            settings={'tts_url':'http://tts','voice_name':'洛茜','model_version':'V2ProPlus','reference_audio':str(ref),'reference_text':'旧','prompt_lang':'zh'}
            state=Path(temp)/'state.json';catalog=ModelCatalog(root,settings,state)
            result=catalog.scan();self.assertEqual(result['counts']['total'],2)
            self.assertEqual({x['checkpoint'] for x in result['models']},{'洛茜_e16_s1','洛茜_e20_s1'})
            chosen=next(x for x in result['models'] if 'e20' in x['checkpoint'])
            with patch('server.model_catalog.urllib.request.urlopen',return_value=Reply()) as opened:
                selected=catalog.select(chosen['id'])
            self.assertEqual(opened.call_count,2);self.assertEqual(selected['state'],'available')
            self.assertEqual(settings['reference_audio'],str(ref));self.assertEqual(settings['reference_text'],'中文参考')
            self.assertEqual(json.loads(state.read_text(encoding='utf-8'))['model_id'],chosen['id'])
            self.assertEqual(catalog.scan()['counts']['available'],1)

    def test_id_survives_layout_change_and_new_naming_format_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'models/gpt-sovits'
            old=root/'角色甲'/'V2-Pro-Plus';old.mkdir(parents=True)
            (old/'原参数-e10.ckpt').write_bytes(b'gpt')
            (old/'原参数_e10_s1.pth').write_bytes(b'sovits')
            (old/'【默认】参考.mp3').write_bytes(b'audio')
            (old/'头像.png').write_bytes(b'image')
            settings={'tts_url':'http://tts','voice_name':'角色甲','model_version':'V2-Pro-Plus'}
            catalog=ModelCatalog(root,settings,Path(temp)/'state.json')
            before=catalog.scan()['models'][0]
            new=root/'任意作品-中文'/'V2-Pro-Plus'/'任意新增分类'/'角色甲'
            new.parent.mkdir(parents=True);old.rename(new)
            after=ModelCatalog(root,settings,Path(temp)/'state2.json').scan()['models'][0]
            self.assertEqual(before['id'],after['id'])
            self.assertEqual(after['name'],'角色甲')
            self.assertEqual(after['version'],'V2-Pro-Plus')
            self.assertEqual(after['reference_text'],'参考')
            self.assertTrue(after['selectable'])

    def test_probe_failure_restores_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'models/gpt-sovits';self.files(root/'作品/v4/旧角色');self.files(root/'作品/v4/新角色')
            settings={'tts_url':'http://tts','voice_name':'旧角色','voice':'旧角色','model_version':'v4','reference_text':'旧文本'}
            catalog=ModelCatalog(root,settings,Path(temp)/'state.json');result=catalog.scan();old=catalog.active_id
            new=next(x for x in result['models'] if x['name']=='新角色')
            with patch('server.model_catalog.urllib.request.urlopen',return_value=Reply()):
                with self.assertRaisesRegex(RuntimeError,'试读坏了'):catalog.select(new['id'],probe=lambda:(_ for _ in ()).throw(ValueError('试读坏了')))
            self.assertEqual(catalog.active_id,old);self.assertEqual(settings['voice_name'],'旧角色')
            self.assertEqual(catalog.scan()['models'][1 if result['models'][1]['id']==new['id'] else 0]['state'],'failed')
    def test_load_failure_is_visible_and_can_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'models/gpt-sovits';self.files(root/'作品/v4/角色')
            catalog=ModelCatalog(root,{'tts_url':'http://tts','voice_name':'角色','model_version':'v4'},Path(temp)/'state.json')
            model_id=catalog.scan()['models'][0]['id']
            with patch('server.model_catalog.urllib.request.urlopen',side_effect=OSError('坏权重')):
                with self.assertRaisesRegex(RuntimeError,'坏权重'):catalog.select(model_id)
            failed=catalog.scan()['models'][0]
            self.assertEqual(failed['state'],'failed');self.assertTrue(failed['selectable']);self.assertIn('坏权重',failed['errors'][0])

if __name__=='__main__':unittest.main()
