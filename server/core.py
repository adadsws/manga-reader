# OCR 行适配与有界会话；分镜算法由固定上游提供。
import re
import time
import uuid
import threading
import statistics
from functools import lru_cache
from opencc import OpenCC
from collections import OrderedDict


def box_area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def intersection_area(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )


def select_panel_candidates(boxes, scores):
    """保留可靠画格，仅补回由可靠大格充分包围的中等置信度小格。"""
    strong = [b for b, score in zip(boxes, scores) if score > 0.3]
    return [
        b
        for b, score in zip(boxes, scores)
        if score > 0.3
        or (
            score > 0.2
            and any(
                0.15 <= box_area(b) / max(1, box_area(parent)) <= 0.7
                and intersection_area(b, parent) / max(1, box_area(b)) >= 0.9
                for parent in strong
            )
        )
    ]


def map_text_to_panels(text_boxes, panels):
    """充分包含文字时优先小格；跨框旁白按覆盖面积，无交叠按框间距离。"""
    result = []
    for text in text_boxes:
        if not panels:
            result.append(-1)
            continue
        overlap = [intersection_area(text, p) for p in panels]
        contained = [
            i for i, area in enumerate(overlap) if area / max(1, box_area(text)) >= 0.8
        ]
        if contained:
            result.append(min(contained, key=lambda i: (box_area(panels[i]), i)))
        elif max(overlap) > 0:
            result.append(max(range(len(panels)), key=lambda i: overlap[i]))
        else:

            def distance(i):
                p = panels[i]
                return (
                    max(p[0] - text[2], text[0] - p[2], 0) ** 2
                    + max(p[1] - text[3], text[1] - p[3], 0) ** 2
                )

            result.append(min(range(len(panels)), key=distance))
    return result


def filter_outside_sfx(rows, text_regions):
    """保守跳过气泡外拟声候选，无法确认的中文/英文继续保留。"""
    # Magi 文本区域提供对白位置证据；不以其对白分类单独删除中文旁白。
    typical = (
        statistics.median(
            [
                min(r["box"][2] - r["box"][0], r["box"][3] - r["box"][1])
                for r in rows
                if r["score"] >= 0.8
            ]
        )
        if any(r["score"] >= 0.8 for r in rows)
        else float("inf")
    )
    kept, excluded = [], []
    for row in rows:
        b = row["box"]
        coverage = max(
            (
                intersection_area(b, region["box"]) / max(1, box_area(b))
                for region in text_regions
            ),
            default=0,
        )
        kana = bool(re.search(r"[ぁ-ゖァ-ヺｦ-ﾟ]", row["text"]))
        # 日文艺术字常被中文 OCR 误读成拉丁字母：还须同时满足低置信度和大字形。
        distorted = (
            row["score"] < 0.8
            and bool(re.search(r"[A-Za-z]", row["text"]))
            and min(b[2] - b[0], b[3] - b[1]) >= typical * 2
        )
        if text_regions and coverage < 0.15 and (kana or distorted):
            excluded.append(
                dict(
                    row,
                    reason="outside_japanese_sfx_candidate",
                    text_region_coverage=coverage,
                )
            )
        else:
            kept.append(row)
    return kept, excluded


def filter_speech_candidates(rows, text_regions):
    """对白候选复核：保留原始行和排除原因，不使用漫画词语黑名单。"""
    kept, excluded = filter_outside_sfx(rows, text_regions)
    result = []
    kana_rows = [r for r in rows if re.search(r"[ぁ-ゖァ-ヺｦ-ﾟ]", r["text"])]
    for row in kept:
        b, text = row["box"], row["text"]
        han = len(re.findall(r"[\u3400-\u9fff]", text))
        matches = [(intersection_area(b, t["box"]) / max(1, box_area(b)), t.get("dialogue_score")) for t in text_regions]
        coverage, dialogue = max(matches, key=lambda x: x[0], default=(0, None))
        # 有文字区域和邻近长中文行双重支持的高置信短行，不因附近拟声字删除。
        supported_short = row["score"] >= .98 and coverage >= .75 and any(
            other is not row and other["score"] >= .9
            and len(re.findall(r"[\u3400-\u9fff]",other["text"])) >= 4
            and max(other["box"][0]-b[2], b[0]-other["box"][2],0) <= max(b[2]-b[0],b[3]-b[1])*1.5
            and max(other["box"][1]-b[3], b[1]-other["box"][3],0) <= max(b[2]-b[0],b[3]-b[1])*1.5
            for other in rows)
        reason = None
        if not han and re.search(r"[A-Za-zＡ-Ｚａ-ｚ]", text):
            reason = "english_skipped"
        elif not han and re.search(r"[ぁ-ゖァ-ヺｦ-ﾟ]", text):
            reason = "kana_without_chinese"
        elif not han and re.search(r"[0-9０-９]", text) and text_regions and (coverage < .15 or (dialogue is not None and dialogue < .4)):
            reason = "isolated_number_sfx_candidate"
        elif not supported_short and han and han <= 2 and coverage >= .3 and dialogue is not None and dialogue < (.45 if han == 1 else .35):
            reason = "short_non_dialogue_candidate"
        elif not supported_short and 0 < han <= 2 and dialogue is not None and dialogue < .65 and any(
            max(k["box"][0]-b[2], b[0]-k["box"][2], 0) <= min(b[2]-b[0], k["box"][2]-k["box"][0])
            and max(0, min(b[3], k["box"][3])-max(b[1], k["box"][1])) / max(1, min(b[3]-b[1], k["box"][3]-k["box"][1])) > .25
            for k in kana_rows
        ):
            reason = "short_text_in_kana_cluster"
        elif 0 < han <= 3 and text_regions and coverage < .15:
            near_kana = any(max(k["box"][0]-b[2], b[0]-k["box"][2], 0) <= 50 and max(k["box"][1]-b[3], b[1]-k["box"][3], 0) <= 100 for k in kana_rows)
            if near_kana:
                reason = "outside_text_near_kana"
        if reason:
            excluded.append(dict(row, reason=reason, text_region_coverage=coverage, dialogue_score=dialogue))
        else:
            result.append(row)
    return result, excluded


def compact_reading_order(groups):
    """Magi 右上角距离顺序；大字形/长框使距离失真时返回 None 使用原排序。"""
    if not groups:
        return []
    boxes = [g["box"] for g in groups]
    typical = statistics.median(min(b[2]-b[0], b[3]-b[1]) for b in boxes)
    if any(b[3]-b[1] > 6 * typical for b in boxes):
        return None
    right, top = max(b[2] for b in boxes), min(b[1] for b in boxes)
    return sorted(range(len(groups)), key=lambda i: ((right-boxes[i][2])**2 + (boxes[i][1]-top)**2, i))


def split_sentences(text):
    return [
        s.strip()
        for s in re.findall(r"[^。！？!?\n]+[。！？!?]*|[。！？!?]+", text)
        if s.strip()
    ]


@lru_cache(maxsize=1)
def speech_converter():
    return OpenCC("t2s")


def normalize_speech_text(text):
    """统一朗读入口：繁简转换、跳过英文词和假名，保留中文与数字语境。"""
    text = speech_converter().convert(text)
    # 横排、竖排及点串省略号统一；单个句号与小数点保持不变。
    text = re.sub(r"[…⋯︙⋮⋰⋱‥]+", "...", text)
    text = re.sub(r"[.．。·•∙⋅](?:[ \t\u3000]*[.．。·•∙⋅])+", "...", text)
    text = re.sub(r"[A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ0-9０-９'’_-]*", "", text)
    text = re.sub(r"[ぁ-ゖァ-ヺｦ-ﾟー]+", "", text)
    if not re.search(r"[\u3400-\u9fff0-9０-９]", text):
        return ""
    text = re.sub(
        r"[♡♥❤❣💕💖💗💘💙💚💛💜🖤🤍🤎💝💞💟🩷🩵🩶][\ufe0e\ufe0f]?", "！", text
    )
    # 仅匹配完整短句，不把逗号分隔的从句或已有问号、省略号改写。
    return re.sub(
        r"(?<![^。！？!?\n])(\s*[「“『]?)([\u3400-\u9fff]{1,2})([」”』]?)(?:。|$)",
        lambda m: m[1] + m[2] + "！" + m[3],
        text,
    )


def speech_chunks(text, limit=24):
    """有限长、按停顿分块；每个字符仅进入一个请求，整页仍返回一个音频。"""
    text = normalize_speech_text(text)
    units = re.findall(r"[^。！？!?；;，,：:…]+[。！？!?；;，,：:…]*|[。！？!?；;，,：:…]+", text)
    chunks, current = [], ""
    for unit in units:
        while len(unit) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:limit])
            unit = unit[limit:]
        if len(current) + len(unit) > limit:
            chunks.append(current)
            current = ""
        current += unit
    if current:
        chunks.append(current)
    return chunks


def merge_page(rows):
    """按已有阅读顺序合并整页；保留原分段供校对，TTS 不传换行。"""
    if not rows:
        return []

    def join(field):
        parts = []
        for row in rows:
            text = " ".join(row.get(field, row["text"]).splitlines()).strip()
            if field == "text":
                text = normalize_speech_text(text)
            if text:
                parts.append(
                    text
                    if text.rstrip("」”』").endswith("...") or text.rstrip("」”』")[-1:] in "。！？!?…；;，,：:、"
                    else text + "。"
                )
        return "".join(parts)

    text = join("text")
    if not text:
        return []
    return [
        {
            "text": text,
            "original": join("original"),
            "review_text": "\n".join(row["text"] for row in rows),
            "segments": rows,
            "excluded_texts": rows[0].get("excluded_texts", []),
            "box": [],
            "confidence": min(row.get("confidence", 1.0) for row in rows),
            "speaker_id": rows[0].get("speaker_id", "default"),
            "mode": "page",
        }
    ]


def prepare_bubbles(rows, voice):
    """保留排序后的完整区域，不再按标点拆句或合并整页。"""
    result = []
    excluded = [item for row in rows for item in row.get("excluded_texts", [])]
    for index, row in enumerate(rows):
        text = normalize_speech_text(row["text"])
        if not text.strip():
            continue
        item = dict(row, text=text, speaker_id=voice, mode="bubble", group_id=index)
        item.pop("excluded_texts", None)
        result.append(item)
    if result:
        result[0]["excluded_texts"] = excluded
    return result


def prepare_panels(rows, voice):
    """按已有分镜归属合并相邻气泡，保留板块内顺序；未知归属不跨区猜测。"""
    bubbles = prepare_bubbles(rows, voice)
    batches = []
    for row in bubbles:
        panel = row.get("panel_id")
        if batches and panel is not None and batches[-1][0].get("panel_id") == panel:
            batches[-1].append(row)
        else:
            batches.append([row])
    result = []
    for group in batches:
        panel = group[0].get("panel_id")
        if panel is None:
            result.append(dict(group[0], group_id=len(result), segmentation="no_panel_bubble_fallback"))
            continue
        unit = merge_page(group)[0]
        unit.update(mode="panel", group_id=len(result), panel_id=panel,
                    box=group[0]["panel_box"], panel_box=group[0]["panel_box"],
                    bubble_count=len(group), segmentation="panel_with_ordered_bubbles")
        result.append(unit)
    return result


def prepare_panel_sentences(rows, voice):
    """先按板块合并，再仅按中文句号分段；保留逗号、顿号、问叹号和省略号。"""
    result = []
    for panel in prepare_panels(rows, voice):
        text = panel["text"]
        for index, match in enumerate(re.finditer(r"[^。]+(?:。+|$)", text)):
            if not normalize_speech_text(match.group()).strip():
                continue
            unit = dict(panel, text=match.group(), mode="panel_sentence",
                        group_id=len(result), sentence_index=index,
                        panel_text=text, text_range=[match.start(), match.end()],
                        original_scope="panel", segmentation="panel_then_chinese_period")
            if index:
                unit.pop("excluded_texts", None)
            result.append(unit)
    return result


def group_lines(rows, text_regions=None, panels=None):
    # 优先保留模型文字区域；相接气泡不能仅因文字靠近而跨区域合并。
    regions = text_regions or []
    owners = []
    for row in rows:
        box = row["box"]
        candidates = []
        for index, region in enumerate(regions):
            coverage = intersection_area(box, region["box"]) / max(1, box_area(box))
            if coverage >= 0.6:
                candidates.append((coverage, -box_area(region["box"]), index))
        owners.append(max(candidates)[2] if candidates else None)
    panel_ids = map_text_to_panels([r["box"] for r in rows], panels) if panels else [None] * len(rows)
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i, a in enumerate(rows):
        x, y, r, b = a["box"]
        w = r - x
        h = b - y
        for j in range(i + 1, len(rows)):
            X, Y, R, B = rows[j]["box"]
            W = R - X
            H = B - Y
            vertical = h > w * 1.5 and H > W * 1.5
            if vertical:
                overlap = max(0, min(b, B) - max(y, Y)) / max(1, min(h, H))
                gap = max(0, max(x, X) - min(r, R))
                same = (
                    overlap > 0.4
                    and gap < min(w, W) * 0.75
                    and abs(y - Y) < max(w, W) * 1.5
                )
            else:
                overlap = max(0, min(r, R) - max(x, X)) / max(1, min(w, W))
                gap = max(0, max(y, Y) - min(b, B))
                same = (
                    h <= w * 1.5
                    and H <= W * 1.5
                    and overlap > 0.55
                    and gap < min(h, H) * 0.65
                )
            if panel_ids[i] != panel_ids[j]:
                same = False
            elif owners[i] is not None or owners[j] is not None:
                # 同一区域容纳错位文字或连接气泡；不同区域保持独立。
                same = owners[i] is not None and owners[i] == owners[j]
            if same:
                parent[find(j)] = find(i)
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)
    result = []
    for group in groups.values():
        vertical = (
            sum(
                (r["box"][3] - r["box"][1]) > (r["box"][2] - r["box"][0]) * 1.5
                for r in group
            )
            > len(group) / 2
        )
        group.sort(
            key=lambda r: (-r["box"][0], r["box"][1])
            if vertical
            else (r["box"][1], r["box"][0])
        )
        if regions and len(group) > 1:
            # 共用一个检测区域的连接气泡先保留局部文字块，再排序块，避免列交错。
            parts = group_lines(group)
            order = compact_reading_order(parts)
            if order is None:
                order = sorted(range(len(parts)), key=lambda i: (-parts[i]["box"][0], parts[i]["box"][1])) if vertical else sorted(range(len(parts)), key=lambda i: (parts[i]["box"][1], parts[i]["box"][0]))
            group = [column for i in order for column in parts[i]["columns"]]
        # 文字列直接相连，只保留OCR本身的标点，不人为增加停顿。
        result.append(
            {
                "original": "".join(r["text"] for r in group),
                "columns": [dict(r) for r in group],
                "box": [
                    min(r["box"][0] for r in group),
                    min(r["box"][1] for r in group),
                    max(r["box"][2] for r in group),
                    max(r["box"][3] for r in group),
                ],
                "score": min(r["score"] for r in group),
            }
        )
    return result


class PageStore:
    def __init__(self, limit=12):
        self.limit = limit
        self.pages = OrderedDict()
        self.lock = threading.RLock()

    def add(self, sentences, **metadata):
        with self.lock:
            key = uuid.uuid4().hex
            self.pages[key] = {
                "sentences": sentences,
                "created": time.time(),
                "audio": {},
                "audio_modes": {},
                **metadata,
            }
            while len(self.pages) > self.limit:
                self.pages.popitem(last=False)
            return key

    def get(self, key):
        with self.lock:
            p = self.pages.get(key)
            if p and time.time() - p["created"] > 3600:
                self.pages.pop(key, None)
                return None
            return p

    def cancel(self, key):
        with self.lock:
            self.pages.pop(key, None)

    def clear(self):
        with self.lock:
            self.pages.clear()
