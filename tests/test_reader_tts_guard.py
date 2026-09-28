# 完整性保护覆盖所有启用的朗读单元；验证失败重试、无重复拼接及取消。
import io
import unittest
import wave
from unittest.mock import patch
import server.tts_guard as tts_guard
from server.tts_guard import coverage, guarded_synthesize, recovery_parts

def wav(number):
    output=io.BytesIO()
    with wave.open(output,'wb') as stream:
        stream.setparams((1,2,16000,0,'NONE','not compressed'))
        stream.writeframes(bytes([number,0])*160)
    return output.getvalue()

class GuardTests(unittest.TestCase):
    def test_asr_initialization_failure_does_not_retry_on_cpu(self):
        with patch.object(tts_guard, '_model', None), patch.object(tts_guard, '_model_device', None), \
             patch('server.tts_guard._load_model', side_effect=RuntimeError('CUDA 初始化失败')) as load:
            with self.assertRaisesRegex(RuntimeError, 'CUDA 初始化失败'):
                tts_guard.recognize(wav(1))
        load.assert_called_once_with('cuda')

    def test_asr_inference_failure_does_not_reload_cpu_model(self):
        class BrokenModel:
            def generate(self, **_kwargs): raise RuntimeError('CUDA 推理失败')
        with patch.object(tts_guard, '_model', BrokenModel()), patch.object(tts_guard, '_model_device', 'cuda'), \
             patch('server.tts_guard._load_model') as load:
            with self.assertRaisesRegex(RuntimeError, 'CUDA 推理失败'):
                tts_guard.recognize(wav(1))
        load.assert_not_called()

    def test_missing_head_or_tail_is_rejected(self):
        expected='那是什么...嗯？是苹果啊。'
        for text in ('那是什么','嗯是苹果啊'):
            self.assertFalse(coverage(expected,text)['passed'])
        self.assertTrue(coverage(expected,'那是什么恩是苹果呀')['passed'])

    def test_homophones_and_vocalizations_do_not_create_false_failures(self):
        self.assertTrue(coverage('琴都酱啊...','青豆酱')['passed'])
        self.assertTrue(coverage('琴都酱啊...','琴都叫了')['passed'])
        self.assertTrue(coverage('普通句子。','普通剂子')['passed'])
        self.assertTrue(coverage('一粒一粒很难画得平均。','一粒一粒很难画的平静')['passed'])
        self.assertTrue(coverage('哈！哈！','啊')['passed'])
        self.assertFalse(coverage('不要！最后不行！','不要')['passed'])

    def test_unexpected_semantic_prefix_or_suffix_is_rejected_generically(self):
        expected='今天去公园散步。'
        for text in ('必须保守秘密今天去公园散步', '今天去公园散步然后公布计划'):
            result=coverage(expected,text)
            self.assertFalse(result['passed'])
            self.assertLess(result['phonetic_precision'],.7)
        self.assertTrue(coverage(expected,'今天去公园里散步')['passed'])

    def test_unexpected_content_triggers_normal_seed_retry(self):
        calls=[]
        text='今天去公园散步。'
        with patch('server.tts_guard.recognize',side_effect=['必须保守秘密今天去公园散步','今天去公园散步']):
            _,audit=guarded_synthesize(text,lambda value,seed:calls.append((value,seed)) or wav(1))
        self.assertEqual(calls,[(text,42),(text,0)])
        self.assertEqual([record['coverage']['passed'] for record in audit['attempts']],[False,True])
        self.assertTrue(audit['recovered'])
        self.assertTrue(audit['verified'])

    def test_recovery_parts_are_lossless_and_not_punctuation_specific(self):
        text='先看看...这是什么？原来如此。'
        self.assertEqual(recovery_parts(text), ['先看看...', '这是什么？原来如此。'])
        self.assertEqual(''.join(recovery_parts(text)), text)
        self.assertEqual(recovery_parts('先走！再回来？'), ['先走！', '再回来？'])
        self.assertEqual(recovery_parts('这是完整的句子'), ['这是完整的句子'])
        self.assertEqual(recovery_parts('第一段...第二段...！'), ['第一段...', '第二段...！'])

    def test_recovery_retains_only_accepted_audio(self):
        calls=[]
        def synth(text,seed):
            calls.append((text,seed));return wav(len(calls))
        text='那是什么...嗯？是苹果啊。'
        with patch('server.tts_guard.recognize',side_effect=['那是什么','那是什么','啊','嗯是苹果啊']):
            body,audit=guarded_synthesize(text,synth)
        self.assertEqual([call[1] for call in calls],[42,42,42,0])
        self.assertEqual(''.join(record['text'] for record in audit['requests']),text)
        self.assertEqual(len(audit['attempts']),4)
        with wave.open(io.BytesIO(body)) as stream:
            self.assertEqual(stream.readframes(stream.getnframes()),bytes([2,0])*160+bytes([4,0])*160)

    def test_good_initial_result_is_not_resynthesized(self):
        with patch('server.tts_guard.recognize',return_value='那是什么嗯是苹果啊'):
            _,audit=guarded_synthesize('那是什么...嗯？是苹果啊。',lambda text,seed:wav(1))
        self.assertFalse(audit['recovered']);self.assertEqual(len(audit['attempts']),1)
        self.assertIn('synthesis_seconds',audit['attempts'][0])
        self.assertIn('asr_seconds',audit['attempts'][0])

    def test_exhausted_retries_return_complete_initial_audio_as_unverified(self):
        calls=[]
        with patch('server.tts_guard.recognize',return_value='无关'):
            body,audit=guarded_synthesize('先看看...这是什么？',lambda text,seed:calls.append(seed) or wav(1))
        self.assertEqual(calls,[42,42,0,7,42,0,7])
        self.assertEqual(body,wav(1))
        self.assertFalse(audit['verified'])
        self.assertEqual(audit['fallback'],'initial_complete_wav')
        self.assertEqual(len(audit['requests']),1)

    def test_partial_recovery_keeps_verified_main_phrase_when_vocalization_is_unverified(self):
        calls=[]
        def synth(text,seed):
            calls.append((text,seed));return wav(len(calls))
        text='我喜欢小绀！？·呜嗯。'
        with patch('server.tts_guard.recognize',side_effect=['哦','我喜欢小盖','哎呀','嗯','嗯']):
            body,audit=guarded_synthesize(text,synth)
        self.assertEqual(calls,[(text,42),('我喜欢小绀！？',42),('·呜嗯。',42),('·呜嗯。',0),('·呜嗯。',7)])
        self.assertEqual(''.join(record['text'] for record in audit['requests']),text)
        self.assertTrue(audit['recovered'])
        self.assertTrue(audit['partial_recovery'])
        self.assertFalse(audit['verified'])
        self.assertEqual(audit['unverified_parts'],1)
        self.assertEqual(audit['fallback'],'per_part_complete_wav')
        with wave.open(io.BytesIO(body)) as stream:
            self.assertEqual(stream.readframes(stream.getnframes()),bytes([2,0])*160+bytes([3,0])*160)

    def test_partial_recovery_is_generic_for_any_failed_part_position(self):
        calls=[]
        def synth(text,seed):
            calls.append((text,seed));return wav(len(calls))
        text='第一段！第二段？第三段。'
        # 整句失败；第一、三段通过；中间段三种种子均未通过。
        heard=['无关','第一段','无关','无关','无关','第三段']
        with patch('server.tts_guard.recognize',side_effect=heard):
            body,audit=guarded_synthesize(text,synth)
        self.assertEqual([record['text'] for record in audit['requests']],['第一段！','第二段？','第三段。'])
        self.assertEqual([record['coverage']['passed'] for record in audit['requests']],[True,False,True])
        self.assertTrue(audit['partial_recovery'])
        self.assertEqual(audit['unverified_parts'],1)
        with wave.open(io.BytesIO(body)) as stream:
            self.assertEqual(stream.readframes(stream.getnframes()),bytes([2,0])*160+bytes([3,0])*160+bytes([6,0])*160)

    def test_single_sentence_retries_whole_unit_with_new_seeds(self):
        calls=[]
        with patch('server.tts_guard.recognize',side_effect=['无关','无关','完整句子']):
            _,audit=guarded_synthesize('完整句子。',lambda text,seed:calls.append((text,seed)) or wav(1))
        self.assertEqual(calls,[('完整句子。',42),('完整句子。',0),('完整句子。',7)])
        self.assertTrue(audit['recovered'])

    def test_cancelled_after_recognition_discards_audio(self):
        cancelled=[False]
        def recognize(_body):
            cancelled[0]=True;return '那是什么嗯是苹果啊'
        with patch('server.tts_guard.recognize',side_effect=recognize):
            with self.assertRaisesRegex(ValueError,'取消'):
                guarded_synthesize('那是什么...嗯？是苹果啊。',lambda text,seed:wav(1),cancelled=lambda:cancelled[0])

if __name__=='__main__':unittest.main()
