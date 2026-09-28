import torch
import numpy as np
import random
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from shapely.geometry import Point, box
import networkx as nx
from copy import deepcopy
from itertools import groupby

def move_to_device(inputs, device):
    if hasattr(inputs, "keys"):
        return {k: move_to_device(v, device) for k, v in inputs.items()}
    elif isinstance(inputs, list):
        return [move_to_device(v, device) for v in inputs]
    elif isinstance(inputs, tuple):
        return tuple([move_to_device(v, device) for v in inputs])
    elif isinstance(inputs, np.ndarray):
        return torch.from_numpy(inputs).to(device)
    else:
        return inputs.to(device)

class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.size = [1] * n
        self.num_components = n

    @classmethod
    def from_adj_matrix(cls, adj_matrix):
        ufds = cls(adj_matrix.shape[0])
        for i in range(adj_matrix.shape[0]):
            for j in range(adj_matrix.shape[1]):
                if adj_matrix[i, j] > 0:
                    ufds.unite(i, j)
        return ufds
    
    @classmethod
    def from_adj_list(cls, adj_list):
        ufds = cls(len(adj_list))
        for i in range(len(adj_list)):
            for j in adj_list[i]:
                ufds.unite(i, j)
        return ufds
    
    @classmethod
    def from_edge_list(cls, edge_list, num_nodes):
        ufds = cls(num_nodes)
        for edge in edge_list:
            ufds.unite(edge[0], edge[1])
        return ufds

    def find(self, x):
        if self.parent[x] == x:
            return x
        self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def unite(self, x, y):
        x = self.find(x)
        y = self.find(y)
        if x != y:
            if self.size[x] < self.size[y]:
                x, y = y, x
            self.parent[y] = x
            self.size[x] += self.size[y]
            self.num_components -= 1
    
    def get_components_of(self, x):
        x = self.find(x)
        return [i for i in range(len(self.parent)) if self.find(i) == x]
    
    def are_connected(self, x, y):
        return self.find(x) == self.find(y)

    def get_size(self, x):
        return self.size[self.find(x)]

    def get_num_components(self):
        return self.num_components
    
    def get_labels_for_connected_components(self):
        map_parent_to_label = {}
        labels = []
        for i in range(len(self.parent)):
            parent = self.find(i)
            if parent not in map_parent_to_label:
                map_parent_to_label[parent] = len(map_parent_to_label)
            labels.append(map_parent_to_label[parent])
        return labels

def visualise_single_image_prediction(image_as_np_array, predictions, filename):
    figure, subplot = plt.subplots(1, 1, figsize=(10, 10))
    subplot.imshow(image_as_np_array)
    plot_bboxes(subplot, predictions["panels"], color="green")
    plot_bboxes(subplot, predictions["texts"], color="red", visibility=predictions["is_essential_text"])
    plot_bboxes(subplot, predictions["characters"], color="blue")
    plot_bboxes(subplot, predictions["tails"], color="purple")

    for i, name in enumerate(predictions["character_names"]):
        char_bbox = predictions["characters"][i]
        x1, y1, x2, y2 = char_bbox
        subplot.text(x1, y1 - 2, name, 
            verticalalignment='bottom', horizontalalignment='left',
            bbox=dict(facecolor='blue', alpha=1, edgecolor='none'),  # Background settings
            color='white', fontsize=8)

    COLOURS = [
        "#b7ff51", # green
        "#f50a8f", # pink
        "#4b13b6", # purple
        "#ddaa34", # orange
        "#bea2a2", # brown
    ]
    colour_index = 0
    character_cluster_labels = predictions["character_cluster_labels"]
    unique_label_sorted_by_frequency = sorted(list(set(character_cluster_labels)), key=lambda x: character_cluster_labels.count(x), reverse=True)
    for label in unique_label_sorted_by_frequency:
        root = None
        others = []
        for i in range(len(predictions["characters"])):
            if character_cluster_labels[i] == label:
                if root is None:
                    root = i
                else:
                    others.append(i)
        if colour_index >= len(COLOURS):
            random_colour = COLOURS[0]
            while random_colour in COLOURS:
                random_colour = "#" + "".join([random.choice("0123456789ABCDEF") for j in range(6)])
        else:
            random_colour = COLOURS[colour_index]
            colour_index += 1
        bbox_i = predictions["characters"][root]
        x1 = bbox_i[0] + (bbox_i[2] - bbox_i[0]) / 2
        y1 = bbox_i[1] + (bbox_i[3] - bbox_i[1]) / 2
        subplot.plot([x1], [y1], color=random_colour, marker="o", markersize=5)
        for j in others:
            # draw line from centre of bbox i to centre of bbox j
            bbox_j = predictions["characters"][j]
            x1 = bbox_i[0] + (bbox_i[2] - bbox_i[0]) / 2
            y1 = bbox_i[1] + (bbox_i[3] - bbox_i[1]) / 2
            x2 = bbox_j[0] + (bbox_j[2] - bbox_j[0]) / 2
            y2 = bbox_j[1] + (bbox_j[3] - bbox_j[1]) / 2
            subplot.plot([x1, x2], [y1, y2], color=random_colour, linewidth=2)
            subplot.plot([x2], [y2], color=random_colour, marker="o", markersize=5)
    
    for (i, j) in predictions["text_character_associations"]:
        bbox_i = predictions["texts"][i]
        bbox_j = predictions["characters"][j]
        if not predictions["is_essential_text"][i]:
            continue
        x1 = bbox_i[0] + (bbox_i[2] - bbox_i[0]) / 2
        y1 = bbox_i[1] + (bbox_i[3] - bbox_i[1]) / 2
        x2 = bbox_j[0] + (bbox_j[2] - bbox_j[0]) / 2
        y2 = bbox_j[1] + (bbox_j[3] - bbox_j[1]) / 2
        subplot.plot([x1, x2], [y1, y2], color="red", linewidth=2, linestyle="dashed")
    
    for (i, j) in predictions["text_tail_associations"]:
        bbox_i = predictions["texts"][i]
        bbox_j = predictions["tails"][j]
        x1 = bbox_i[0] + (bbox_i[2] - bbox_i[0]) / 2
        y1 = bbox_i[1] + (bbox_i[3] - bbox_i[1]) / 2
        x2 = bbox_j[0] + (bbox_j[2] - bbox_j[0]) / 2
        y2 = bbox_j[1] + (bbox_j[3] - bbox_j[1]) / 2
        subplot.plot([x1, x2], [y1, y2], color="purple", linewidth=2, linestyle="dashed")

    subplot.axis("off")
    if filename is not None:
        plt.savefig(filename, bbox_inches="tight", pad_inches=0)

    figure.canvas.draw()
    image = np.array(figure.canvas.renderer._renderer)
    plt.close()
    return image

def plot_bboxes(subplot, bboxes, color="red", visibility=None):
    if visibility is None:
        visibility = [1] * len(bboxes)
    for id, bbox in enumerate(bboxes):
        if visibility[id] == 0:
            continue
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        rect = patches.Rectangle(
            bbox[:2], w, h, linewidth=1, edgecolor=color, facecolor="none", linestyle="solid"
        )
        subplot.add_patch(rect)

def sort_panels(rects):
    before_rects = convert_to_list_of_lists(rects)
    # slightly erode all rectangles initially to account for imperfect detections
    rects = [erode_rectangle(rect, 0.05) for rect in before_rects]
    G = nx.DiGraph()
    G.add_nodes_from(range(len(rects)))
    for i in range(len(rects)):
        for j in range(len(rects)):
            if i == j:
                continue
            if is_there_a_directed_edge(i, j, rects):
                G.add_edge(i, j, weight=get_distance(rects[i], rects[j]))
            else:
                G.add_edge(j, i, weight=get_distance(rects[i], rects[j]))
    while True:
        cycles = sorted(nx.simple_cycles(G))
        cycles = [cycle for cycle in cycles if len(cycle) > 1]
        if len(cycles) == 0:
            break
        cycle = cycles[0]
        edges = [e for e in zip(cycle, cycle[1:] + cycle[:1])]
        max_cyclic_edge = max(edges, key=lambda x: G.edges[x]["weight"])
        G.remove_edge(*max_cyclic_edge)
    return list(nx.topological_sort(G))

def is_strictly_above(rectA, rectB):
    x1A, y1A, x2A, y2A = rectA
    x1B, y1B, x2B, y2B = rectB
    return y2A < y1B

def is_strictly_below(rectA, rectB):
    x1A, y1A, x2A, y2A = rectA
    x1B, y1B, x2B, y2B = rectB
    return y2B < y1A

def is_strictly_left_of(rectA, rectB):
    x1A, y1A, x2A, y2A = rectA
    x1B, y1B, x2B, y2B = rectB
    return x2A < x1B

def is_strictly_right_of(rectA, rectB):
    x1A, y1A, x2A, y2A = rectA
    x1B, y1B, x2B, y2B = rectB
    return x2B < x1A

def intersects(rectA, rectB):
    return box(*rectA).intersects(box(*rectB))

def is_there_a_directed_edge(a, b, rects):
    rectA = rects[a]
    rectB = rects[b]
    centre_of_A = [rectA[0] + (rectA[2] - rectA[0]) / 2, rectA[1] + (rectA[3] - rectA[1]) / 2]
    centre_of_B = [rectB[0] + (rectB[2] - rectB[0]) / 2, rectB[1] + (rectB[3] - rectB[1]) / 2]
    if np.allclose(np.array(centre_of_A), np.array(centre_of_B)):
        return box(*rectA).area > (box(*rectB)).area
    copy_A = [rectA[0], rectA[1], rectA[2], rectA[3]]
    copy_B = [rectB[0], rectB[1], rectB[2], rectB[3]]
    while True:
        if is_strictly_above(copy_A, copy_B) and not is_strictly_left_of(copy_A, copy_B):
            return 1
        if is_strictly_above(copy_B, copy_A) and not is_strictly_left_of(copy_B, copy_A):
            return 0
        if is_strictly_right_of(copy_A, copy_B) and not is_strictly_below(copy_A, copy_B):
            return 1
        if is_strictly_right_of(copy_B, copy_A) and not is_strictly_below(copy_B, copy_A):
            return 0
        if is_strictly_below(copy_A, copy_B) and is_strictly_right_of(copy_A, copy_B):
            return use_cuts_to_determine_edge_from_a_to_b(a, b, rects)
        if is_strictly_below(copy_B, copy_A) and is_strictly_right_of(copy_B, copy_A):
           return use_cuts_to_determine_edge_from_a_to_b(a, b, rects)
        # otherwise they intersect
        copy_A = erode_rectangle(copy_A, 0.05)
        copy_B = erode_rectangle(copy_B, 0.05)
    
def get_distance(rectA, rectB):
    return box(rectA[0], rectA[1], rectA[2], rectA[3]).distance(box(rectB[0], rectB[1], rectB[2], rectB[3]))

def use_cuts_to_determine_edge_from_a_to_b(a, b, rects):
    rects = deepcopy(rects)
    while True:
        xmin, ymin, xmax, ymax = min(rects[a][0], rects[b][0]), min(rects[a][1], rects[b][1]), max(rects[a][2], rects[b][2]), max(rects[a][3], rects[b][3])
        rect_index = [i for i in range(len(rects)) if intersects(rects[i], [xmin, ymin, xmax, ymax])]
        rects_copy = [rect for rect in rects if intersects(rect, [xmin, ymin, xmax, ymax])]
        
        # try to split the panels using a "horizontal" lines
        overlapping_y_ranges = merge_overlapping_ranges([(y1, y2) for x1, y1, x2, y2 in rects_copy])
        panel_index_to_split = {}
        for split_index, (y1, y2) in enumerate(overlapping_y_ranges):
            for i, index in enumerate(rect_index):
                if y1 <= rects_copy[i][1] <= rects_copy[i][3] <= y2:
                    panel_index_to_split[index] = split_index
        
        if panel_index_to_split[a] != panel_index_to_split[b]:
            return panel_index_to_split[a] < panel_index_to_split[b]
        
        # try to split the panels using a "vertical" lines
        overlapping_x_ranges = merge_overlapping_ranges([(x1, x2) for x1, y1, x2, y2 in rects_copy])
        panel_index_to_split = {}
        for split_index, (x1, x2) in enumerate(overlapping_x_ranges[::-1]):
            for i, index in enumerate(rect_index):
                if x1 <= rects_copy[i][0] <= rects_copy[i][2] <= x2:
                    panel_index_to_split[index] = split_index
        if panel_index_to_split[a] != panel_index_to_split[b]:
            return panel_index_to_split[a] < panel_index_to_split[b]
        
        # otherwise, erode the rectangles and try again
        rects = [erode_rectangle(rect, 0.05) for rect in rects]

def erode_rectangle(bbox, erosion_factor):
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx, cy = x1 + w / 2, y1 + h / 2
    if w < h:
        aspect_ratio = w / h
        erosion_factor_width = erosion_factor * aspect_ratio
        erosion_factor_height = erosion_factor
    else:
        aspect_ratio = h / w
        erosion_factor_width = erosion_factor
        erosion_factor_height = erosion_factor * aspect_ratio
    w = w - w * erosion_factor_width
    h = h - h * erosion_factor_height
    x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    return [x1, y1, x2, y2]

def merge_overlapping_ranges(ranges):
    """
    ranges: list of tuples (x1, x2)
    """
    if len(ranges) == 0:
        return []
    ranges = sorted(ranges, key=lambda x: x[0])
    merged_ranges = []
    for i, r in enumerate(ranges):
        if i == 0:
            prev_x1, prev_x2 = r
            continue
        x1, x2 = r
        if x1 > prev_x2:
            merged_ranges.append((prev_x1, prev_x2))
            prev_x1, prev_x2 = x1, x2
        else:
            prev_x2 = max(prev_x2, x2)
    merged_ranges.append((prev_x1, prev_x2))
    return merged_ranges

def sort_text_boxes_in_reading_order(text_bboxes, sorted_panel_bboxes):
    text_bboxes = convert_to_list_of_lists(text_bboxes)
    sorted_panel_bboxes = convert_to_list_of_lists(sorted_panel_bboxes)

    if len(text_bboxes) == 0:
        return []

    def indices_of_same_elements(nums):
        groups = groupby(range(len(nums)), key=lambda i: nums[i])
        return [list(indices) for _, indices in groups]

    panel_id_for_text = get_text_to_panel_mapping(text_bboxes, sorted_panel_bboxes)
    indices_of_texts = list(range(len(text_bboxes)))
    indices_of_texts, panel_id_for_text = zip(*sorted(zip(indices_of_texts, panel_id_for_text), key=lambda x: x[1]))
    indices_of_texts = list(indices_of_texts)
    grouped_indices = indices_of_same_elements(panel_id_for_text)
    for group in grouped_indices:
        subset_of_text_indices = [indices_of_texts[i] for i in group]
        text_bboxes_of_subset = [text_bboxes[i] for i in subset_of_text_indices]
        sorted_subset_indices = sort_texts_within_panel(text_bboxes_of_subset)
        indices_of_texts[group[0] : group[-1] + 1] = [subset_of_text_indices[i] for i in sorted_subset_indices]
    return indices_of_texts

def get_text_to_panel_mapping(text_bboxes, sorted_panel_bboxes):
    text_to_panel_mapping = []
    for text_bbox in text_bboxes:
        shapely_text_polygon = box(*text_bbox)
        all_intersections = []
        all_distances = []
        if len(sorted_panel_bboxes) == 0:
            text_to_panel_mapping.append(-1)
            continue
        for j, annotation in enumerate(sorted_panel_bboxes):
            shapely_annotation_polygon = box(*annotation)
            if shapely_text_polygon.intersects(shapely_annotation_polygon):
                all_intersections.append((shapely_text_polygon.intersection(shapely_annotation_polygon).area, j))
            all_distances.append((shapely_text_polygon.distance(shapely_annotation_polygon), j))
        if len(all_intersections) == 0:
            text_to_panel_mapping.append(min(all_distances, key=lambda x: x[0])[1])
        else:
            text_to_panel_mapping.append(max(all_intersections, key=lambda x: x[0])[1])
    return text_to_panel_mapping

def sort_texts_within_panel(rects):
    smallest_y = float("inf")
    greatest_x = float("-inf")
    for i, rect in enumerate(rects):
        x1, y1, x2, y2 = rect
        smallest_y = min(smallest_y, y1)
        greatest_x = max(greatest_x, x2)
    
    reference_point = Point(greatest_x, smallest_y)

    polygons_and_index = []
    for i, rect in enumerate(rects):
        x1, y1, x2, y2 = rect
        polygons_and_index.append((box(x1,y1,x2,y2), i))
    # sort points by closest to reference point
    polygons_and_index = sorted(polygons_and_index, key=lambda x: reference_point.distance(x[0]))
    indices = [x[1] for x in polygons_and_index]
    return indices

def x1y1wh_to_x1y1x2y2(bbox):
    x1, y1, w, h = bbox
    return [x1, y1, x1 + w, y1 + h]

def x1y1x2y2_to_xywh(bbox):
    x1, y1, x2, y2 = bbox
    return [x1, y1, x2 - x1, y2 - y1]

def convert_to_list_of_lists(rects):
    if isinstance(rects, torch.Tensor):
        return rects.tolist()
    if isinstance(rects, np.ndarray):
        return rects.tolist()
    return [[a, b, c, d] for a, b, c, d in rects]