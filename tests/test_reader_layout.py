# 使用真实安卓相册截图的模型候选与 OCR 坐标回归嵌套画格。
import json
import unittest
from pathlib import Path
from server.core import select_panel_candidates, map_text_to_panels
from server.ocr import Region, sort


class NestedPanelTests(unittest.TestCase):
    def test_actual_three_page_order(self):
        fixtures = json.loads(
            (Path(__file__).parent / "fixtures/nested_panels.json").read_text(
                encoding="utf-8"
            )
        )
        for name, f in fixtures.items():
            with self.subTest(page=name):
                selected = select_panel_candidates(
                    [c["box"] for c in f["candidates"]],
                    [c["score"] for c in f["candidates"]],
                )
                self.assertEqual(sorted(selected), sorted(f["panels"]))
                groups = {}
                mapping = map_text_to_panels([r["box"] for r in f["rows"]], f["panels"])
                for i, panel in enumerate(mapping):
                    groups.setdefault(panel, []).append(
                        Region(dict(f["rows"][i], index=i))
                    )
                actual = [
                    r.data["index"]
                    for p in sorted(groups)
                    for r in sort._simple_sort(groups[p], True)
                ]
                self.assertEqual(actual, f["expected_order"])

    def test_nested_assignment_does_not_depend_on_panel_input_order(self):
        outer, inner = [0, 0, 100, 100], [60, 0, 100, 90]
        text = [70, 10, 90, 30]
        self.assertEqual(map_text_to_panels([text], [outer, inner]), [1])
        self.assertEqual(map_text_to_panels([text], [inner, outer]), [0])
        self.assertEqual(map_text_to_panels([text], []), [-1])

    def test_weak_uncontained_or_duplicate_candidate_is_not_recovered(self):
        outer = [0, 0, 100, 100]
        inner = [60, 5, 95, 95]
        boxes = [outer, inner, [110, 0, 150, 90], [0, 0, 99, 99]]
        self.assertEqual(
            select_panel_candidates(boxes, [0.8, 0.22, 0.25, 0.25]), [outer, inner]
        )


class OutsideSfxTests(unittest.TestCase):
    def test_three_pages_keep_dialogue_and_only_exclude_verified_sfx(self):
        from server.core import filter_outside_sfx

        fixtures = json.loads(
            (Path(__file__).parent / "fixtures/outside_sfx.json").read_text(
                encoding="utf-8"
            )
        )
        for name, f in fixtures.items():
            with self.subTest(page=name):
                kept, excluded = filter_outside_sfx(f["rows"], f["text_regions"])
                self.assertEqual([r["text"] for r in excluded], f["expected_excluded"])
                self.assertEqual(len(kept) + len(excluded), len(f["rows"]))
                if name == "0041":
                    self.assertTrue(any(r["text"] == "不要" for r in kept))

    def test_kana_inside_text_region_and_outside_chinese_are_preserved(self):
        from server.core import filter_outside_sfx

        rows = [
            {"text": "ドン", "box": [0, 0, 20, 40], "score": 0.95},
            {"text": "中文旁白", "box": [100, 0, 120, 80], "score": 0.99},
            {"text": "H", "box": [200, 0, 220, 20], "score": 0.6},
        ]
        self.assertEqual(filter_outside_sfx(rows, [{"box": [0, 0, 25, 45]}])[0], rows)
        kept, excluded = filter_outside_sfx(rows, [{"box": [300, 300, 400, 400]}])
        self.assertEqual([r["text"] for r in excluded], ["ドン"])
        self.assertEqual(filter_outside_sfx(rows, [])[0], rows)
