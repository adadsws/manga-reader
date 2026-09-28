# 验证竖排合并、原文保留与会话隔离的实际行为。
import unittest
from server.core import (
    group_lines,
    split_sentences,
    PageStore,
    merge_page,
    normalize_speech_text,
)


class ReaderCoreTests(unittest.TestCase):
    def test_page_merge_preserves_reading_order_and_review_rows(self):
        rows = [
            {"text": "右边？", "confidence": 0.9},
            {"text": "左边", "confidence": 0.8},
        ]
        page = merge_page(rows)
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0]["text"], "右边？左边！")
        self.assertEqual(page[0]["review_text"], "右边？\n左边")
        self.assertEqual(page[0]["segments"], rows)
        self.assertEqual(page[0]["confidence"], 0.8)
        self.assertEqual(merge_page([]), [])

    def test_page_merge_applies_speech_punctuation_preserving_source(self):
        rows = [
            {"text": "真的？！"},
            {"text": "嗯……♡"},
            {"text": "「不要」"},
            {"text": "下一句"},
        ]
        self.assertEqual(
            merge_page(rows)[0]["text"], "真的？！嗯...！「不要！」下一句。"
        )
        self.assertEqual(
            [r["text"] for r in rows], ["真的？！", "嗯……♡", "「不要」", "下一句"]
        )

    def test_speech_punctuation_and_original_preservation(self):
        self.assertEqual(
            normalize_speech_text("哈。不行。不要。可以吗？嗯……较长的句子。"),
            "哈！不行！不要！可以吗？嗯...较长的句子。",
        )
        self.assertEqual(normalize_speech_text("♡♥❤️不要"), "！！！不要！")
        rows = [{"text": "不要♡", "original": "不要♡"}]
        page = merge_page(rows)[0]
        self.assertEqual(page["text"], "不要！")
        self.assertEqual(page["review_text"], "不要♡")
        self.assertEqual(page["segments"], rows)
        self.assertIn("♡", page["original"])

    def test_vertical_columns_read_right_then_left(self):
        rows = [
            {"box": [100, 20, 120, 100], "text": "出門啊！", "score": 0.9},
            {"box": [125, 20, 145, 80], "text": "為什麼要", "score": 0.9},
        ]
        self.assertEqual(
            [x["original"] for x in group_lines(rows)], ["為什麼要出門啊！"]
        )

    def test_staggered_vertical_columns_keep_sentence_together(self):
        rows = [
            {"box": [1116, 758, 1160, 858], "text": "对舌头", "score": 0.9},
            {"box": [1045, 809, 1088, 935], "text": "做了什么", "score": 0.9},
            {"box": [867, 759, 979, 962], "text": "旁白", "score": 0.9},
        ]
        self.assertEqual(
            [x["original"] for x in group_lines(rows)], ["对舌头做了什么", "旁白"]
        )

    def test_actual_three_columns_keep_pauses_and_question_marks(self):
        rows = [
            {"text": "色色的東西？？", "box": [118, 771, 150, 922], "score": 0.9},
            {"text": "是什麼啊", "box": [145, 771, 177, 864], "score": 0.9},
            {"text": "所以說", "box": [170, 771, 202, 845], "score": 0.9},
        ]
        result = group_lines(rows)
        self.assertEqual(result[0]["original"], "所以說是什麼啊色色的東西？？")
        self.assertEqual(len(result[0]["columns"]), 3)
        rows[2]["text"] = "所以說！"
        self.assertEqual(
            group_lines(rows)[0]["original"], "所以說！是什麼啊色色的東西？？"
        )

    def test_column_join_preserves_recognized_comma(self):
        rows = [
            {"box": [125, 20, 145, 80], "text": "第一列，", "score": 0.9},
            {"box": [100, 20, 120, 100], "text": "第二列。", "score": 0.9},
        ]
        self.assertEqual(group_lines(rows)[0]["original"], "第一列，第二列。")

    def test_separate_bubbles_stay_separate(self):
        rows = [
            {"box": [10, 10, 30, 90], "text": "左", "score": 0.9},
            {"box": [200, 10, 220, 90], "text": "右", "score": 0.9},
        ]
        self.assertEqual(len(group_lines(rows)), 2)

    def test_text_is_not_filtered_by_quotation_or_topic(self):
        self.assertEqual(
            split_sentences("旁白。独白！没有引号也读"),
            ["旁白。", "独白！", "没有引号也读"],
        )

    def test_store_eviction_and_unknown_page(self):
        s = PageStore(limit=2)
        a = s.add([1])
        s.add([2])
        s.add([3])
        self.assertIsNone(s.get(a))

    def test_cancel_removes_page(self):
        s = PageStore()
        a = s.add([1])
        s.cancel(a)
        self.assertIsNone(s.get(a))


if __name__ == "__main__":
    unittest.main()
