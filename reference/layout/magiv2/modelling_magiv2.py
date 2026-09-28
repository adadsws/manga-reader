from transformers import PreTrainedModel, VisionEncoderDecoderModel, ViTMAEModel, ConditionalDetrModel
from transformers.models.conditional_detr.modeling_conditional_detr import (
    ConditionalDetrMLPPredictionHead, 
    ConditionalDetrModelOutput,
    inverse_sigmoid,
)
from .configuration_magiv2 import Magiv2Config
from .processing_magiv2 import Magiv2Processor
from torch import nn
from typing import Optional, List
import torch
from einops import rearrange, repeat
from .utils import move_to_device, visualise_single_image_prediction, sort_panels, sort_text_boxes_in_reading_order
from transformers.image_transforms import center_to_corners_format
from .utils import UnionFind, sort_panels, sort_text_boxes_in_reading_order
import pulp
import scipy
import numpy as np
from scipy.optimize import linear_sum_assignment

class Magiv2Model(PreTrainedModel):
    config_class = Magiv2Config

    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.processor = Magiv2Processor(config)
        if not config.disable_ocr:
            self.ocr_model = VisionEncoderDecoderModel(config.ocr_model_config)
        if not config.disable_crop_embeddings:
            self.crop_embedding_model = ViTMAEModel(config.crop_embedding_model_config)
        if not config.disable_detections:
            self.num_non_obj_tokens = 5
            self.detection_transformer = ConditionalDetrModel(config.detection_model_config)
            self.bbox_predictor = ConditionalDetrMLPPredictionHead(
                input_dim=config.detection_model_config.d_model,
                hidden_dim=config.detection_model_config.d_model,
                output_dim=4, num_layers=3
            )
            self.character_character_matching_head = ConditionalDetrMLPPredictionHead(
                input_dim = 3 * config.detection_model_config.d_model + (2 * config.crop_embedding_model_config.hidden_size if not config.disable_crop_embeddings else 0),
                hidden_dim=config.detection_model_config.d_model,
                output_dim=1, num_layers=3
            )
            self.text_character_matching_head = ConditionalDetrMLPPredictionHead(
                input_dim = 3 * config.detection_model_config.d_model,
                hidden_dim=config.detection_model_config.d_model,
                output_dim=1, num_layers=3
            )
            self.text_tail_matching_head = ConditionalDetrMLPPredictionHead(
                input_dim = 2 * config.detection_model_config.d_model,
                hidden_dim=config.detection_model_config.d_model,
                output_dim=1, num_layers=3
            )
            self.class_labels_classifier = nn.Linear(
                config.detection_model_config.d_model, config.detection_model_config.num_labels
            )
            self.is_this_text_a_dialogue = nn.Linear(
                config.detection_model_config.d_model, 1
            )
            self.matcher = ConditionalDetrHungarianMatcher(
                class_cost=config.detection_model_config.class_cost,
                bbox_cost=config.detection_model_config.bbox_cost,
                giou_cost=config.detection_model_config.giou_cost
            )

    def move_to_device(self, input):
        return move_to_device(input, self.device)
    
    @torch.no_grad()
    def do_chapter_wide_prediction(self, pages_in_order, character_bank, eta=0.75, batch_size=8, use_tqdm=False, do_ocr=True):
        texts = []
        characters = []
        character_clusters = []
        if use_tqdm:
            from tqdm import tqdm
            iterator = tqdm(range(0, len(pages_in_order), batch_size))
        else:
            iterator = range(0, len(pages_in_order), batch_size)
        per_page_results = []
        for i in iterator:
            pages = pages_in_order[i:i+batch_size]
            results = self.predict_detections_and_associations(pages)
            per_page_results.extend([result for result in results])

        texts = [result["texts"] for result in per_page_results]
        characters = [result["characters"] for result in per_page_results]
        character_clusters = [result["character_cluster_labels"] for result in per_page_results]
        assigned_character_names = self.assign_names_to_characters(pages_in_order, characters, character_bank, character_clusters, eta=eta)
        if do_ocr:
            ocr = self.predict_ocr(pages_in_order, texts, use_tqdm=use_tqdm)
        offset_characters = 0
        iteration_over = zip(per_page_results, ocr) if do_ocr else per_page_results
        for iter in iteration_over:
            if do_ocr:
                result, ocr_for_page = iter
                result["ocr"] = ocr_for_page
            else:
                result = iter
            result["character_names"] = assigned_character_names[offset_characters:offset_characters + len(result["characters"])]
            offset_characters += len(result["characters"])
        return per_page_results
        
    
    def assign_names_to_characters(self, images, character_bboxes, character_bank, character_clusters, eta=0.75):
        if len(character_bank["images"]) == 0:
            return ["Other" for bboxes_for_image in character_bboxes for bbox in bboxes_for_image]
        chapter_wide_char_embeddings = self.predict_crop_embeddings(images, character_bboxes)
        chapter_wide_char_embeddings = torch.cat(chapter_wide_char_embeddings, dim=0)
        chapter_wide_char_embeddings = torch.nn.functional.normalize(chapter_wide_char_embeddings, p=2, dim=1).cpu().numpy()
        # create must-link and cannot link constraints from character_clusters
        must_link = []
        cannot_link = []
        offset = 0
        for clusters_per_image in character_clusters:
            for i in range(len(clusters_per_image)):
                for j in range(i+1, len(clusters_per_image)):
                    if clusters_per_image[i] == clusters_per_image[j]:
                        must_link.append((offset + i, offset + j))
                    else:
                        cannot_link.append((offset + i, offset + j))
            offset += len(clusters_per_image)
        character_bank_for_this_chapter = self.predict_crop_embeddings(character_bank["images"], [[[0, 0, x.shape[1], x.shape[0]]] for x in character_bank["images"]])
        character_bank_for_this_chapter = torch.cat(character_bank_for_this_chapter, dim=0)
        character_bank_for_this_chapter = torch.nn.functional.normalize(character_bank_for_this_chapter, p=2, dim=1).cpu().numpy()
        costs = scipy.spatial.distance.cdist(chapter_wide_char_embeddings, character_bank_for_this_chapter)
        none_of_the_above = eta * np.ones((costs.shape[0],1))
        costs = np.concatenate([costs, none_of_the_above], axis=1)
        sense = pulp.LpMinimize
        num_supply, num_demand = costs.shape
        problem = pulp.LpProblem("Optimal_Transport_Problem", sense)
        x = pulp.LpVariable.dicts("x", ((i, j) for i in range(num_supply) for j in range(num_demand)), cat='Binary')
        # Objective Function to minimize
        problem += pulp.lpSum([costs[i][j] * x[(i, j)] for i in range(num_supply) for j in range(num_demand)])
        # each crop must be assigned to exactly one character
        for i in range(num_supply):
            problem += pulp.lpSum([x[(i, j)] for j in range(num_demand)]) == 1, f"Supply_{i}_Total_Assignment"
        # cannot link constraints
        for j in range(num_demand-1):
            for (s1, s2) in cannot_link:
                problem += x[(s1, j)] + x[(s2, j)] <= 1, f"Exclusion_{s1}_{s2}_Demand_{j}"
        # must link constraints
        for j in range(num_demand):
            for (s1, s2) in must_link:
                problem += x[(s1, j)] - x[(s2, j)] == 0, f"Inclusion_{s1}_{s2}_Demand_{j}"
        problem.solve()
        assignments = []
        for v in problem.variables():
            if v.varValue is not None and v.varValue > 0:
                index, assignment = v.name.split("(")[1].split(")")[0].split(",")
                assignment = assignment[1:]
                assignments.append((int(index), int(assignment)))

        labels = np.zeros(num_supply)
        for i, j in assignments:
            labels[i] = j
        
        return [character_bank["names"][int(i)] if i < len(character_bank["names"]) else "Other" for i in labels]

    
    def predict_detections_and_associations(
            self,
            images,
            move_to_device_fn=None,
            character_detection_threshold=0.3,
            panel_detection_threshold=0.2,
            text_detection_threshold=0.3,
            tail_detection_threshold=0.34,
            character_character_matching_threshold=0.65,
            text_character_matching_threshold=0.35,
            text_tail_matching_threshold=0.3,
            text_classification_threshold=0.5,
        ):
        assert not self.config.disable_detections
        move_to_device_fn = self.move_to_device if move_to_device_fn is None else move_to_device_fn
        
        inputs_to_detection_transformer = self.processor.preprocess_inputs_for_detection(images)
        inputs_to_detection_transformer = move_to_device_fn(inputs_to_detection_transformer)
        
        detection_transformer_output = self._get_detection_transformer_output(**inputs_to_detection_transformer)
        predicted_class_scores, predicted_bboxes = self._get_predicted_bboxes_and_classes(detection_transformer_output)

        original_image_sizes = torch.stack([torch.tensor(img.shape[:2]) for img in images], dim=0).to(predicted_bboxes.device)

        batch_scores, batch_labels = predicted_class_scores.max(-1)
        batch_scores = batch_scores.sigmoid()
        batch_labels = batch_labels.long()
        batch_bboxes = center_to_corners_format(predicted_bboxes)

        # scale the bboxes back to the original image size
        if isinstance(original_image_sizes, List):
            img_h = torch.Tensor([i[0] for i in original_image_sizes])
            img_w = torch.Tensor([i[1] for i in original_image_sizes])
        else:
            img_h, img_w = original_image_sizes.unbind(1)
        scale_fct = torch.stack([img_w, img_h, img_w, img_h], dim=1).to(batch_bboxes.device)
        batch_bboxes = batch_bboxes * scale_fct[:, None, :]
        
        batch_panel_indices = self.processor._get_indices_of_panels_to_keep(batch_scores, batch_labels, batch_bboxes, panel_detection_threshold)
        batch_character_indices = self.processor._get_indices_of_characters_to_keep(batch_scores, batch_labels, batch_bboxes, character_detection_threshold)
        batch_text_indices = self.processor._get_indices_of_texts_to_keep(batch_scores, batch_labels, batch_bboxes, text_detection_threshold)
        batch_tail_indices = self.processor._get_indices_of_tails_to_keep(batch_scores, batch_labels, batch_bboxes, tail_detection_threshold)

        predicted_obj_tokens_for_batch = self._get_predicted_obj_tokens(detection_transformer_output)
        predicted_t2c_tokens_for_batch = self._get_predicted_t2c_tokens(detection_transformer_output)
        predicted_c2c_tokens_for_batch = self._get_predicted_c2c_tokens(detection_transformer_output)

        text_character_affinity_matrices = self._get_text_character_affinity_matrices(
            character_obj_tokens_for_batch=[x[i] for x, i in zip(predicted_obj_tokens_for_batch, batch_character_indices)],
            text_obj_tokens_for_this_batch=[x[i] for x, i in zip(predicted_obj_tokens_for_batch, batch_text_indices)],
            t2c_tokens_for_batch=predicted_t2c_tokens_for_batch,
            apply_sigmoid=True,
        )

        character_bboxes_in_batch = [batch_bboxes[i][j] for i, j in enumerate(batch_character_indices)]
        character_character_affinity_matrices = self._get_character_character_affinity_matrices(
            character_obj_tokens_for_batch=[x[i] for x, i in zip(predicted_obj_tokens_for_batch, batch_character_indices)],
            crop_embeddings_for_batch=self.predict_crop_embeddings(images, character_bboxes_in_batch, move_to_device_fn),
            c2c_tokens_for_batch=predicted_c2c_tokens_for_batch,
            apply_sigmoid=True,
        )

        text_tail_affinity_matrices = self._get_text_tail_affinity_matrices(
            text_obj_tokens_for_this_batch=[x[i] for x, i in zip(predicted_obj_tokens_for_batch, batch_text_indices)],
            tail_obj_tokens_for_batch=[x[i] for x, i in zip(predicted_obj_tokens_for_batch, batch_tail_indices)],
            apply_sigmoid=True,
        )

        is_this_text_a_dialogue = self._get_text_classification([x[i] for x, i in zip(predicted_obj_tokens_for_batch, batch_text_indices)])

        results = []
        for batch_index in range(len(batch_scores)):
            panel_indices = batch_panel_indices[batch_index]
            character_indices = batch_character_indices[batch_index]
            text_indices = batch_text_indices[batch_index]
            tail_indices = batch_tail_indices[batch_index]

            character_bboxes = batch_bboxes[batch_index][character_indices]
            panel_bboxes = batch_bboxes[batch_index][panel_indices]
            text_bboxes = batch_bboxes[batch_index][text_indices]
            tail_bboxes = batch_bboxes[batch_index][tail_indices]

            local_sorted_panel_indices = sort_panels(panel_bboxes)
            panel_bboxes = panel_bboxes[local_sorted_panel_indices]
            local_sorted_text_indices = sort_text_boxes_in_reading_order(text_bboxes, panel_bboxes)
            text_bboxes = text_bboxes[local_sorted_text_indices]

            character_character_matching_scores = character_character_affinity_matrices[batch_index]
            text_character_matching_scores = text_character_affinity_matrices[batch_index][local_sorted_text_indices]
            text_tail_matching_scores = text_tail_affinity_matrices[batch_index][local_sorted_text_indices]
            
            is_essential_text = is_this_text_a_dialogue[batch_index][local_sorted_text_indices] > text_classification_threshold
            character_cluster_labels = UnionFind.from_adj_matrix(
                character_character_matching_scores > character_character_matching_threshold
            ).get_labels_for_connected_components()

            if 0 in text_character_matching_scores.shape:
                text_character_associations = torch.zeros((0, 2), dtype=torch.long)
            else:
                most_likely_speaker_for_each_text = torch.argmax(text_character_matching_scores, dim=1)
                text_indices = torch.arange(len(text_bboxes)).type_as(most_likely_speaker_for_each_text)
                text_character_associations = torch.stack([text_indices, most_likely_speaker_for_each_text], dim=1)
                to_keep = text_character_matching_scores.max(dim=1).values > text_character_matching_threshold
                text_character_associations = text_character_associations[to_keep]
            
            if 0 in text_tail_matching_scores.shape:
                text_tail_associations = torch.zeros((0, 2), dtype=torch.long)
            else:
                most_likely_tail_for_each_text = torch.argmax(text_tail_matching_scores, dim=1)
                text_indices = torch.arange(len(text_bboxes)).type_as(most_likely_tail_for_each_text)
                text_tail_associations = torch.stack([text_indices, most_likely_tail_for_each_text], dim=1)
                to_keep = text_tail_matching_scores.max(dim=1).values > text_tail_matching_threshold
                text_tail_associations = text_tail_associations[to_keep]

            results.append({
                "panels": panel_bboxes.tolist(),
                "texts": text_bboxes.tolist(),
                "characters": character_bboxes.tolist(),
                "tails": tail_bboxes.tolist(),
                "text_character_associations": text_character_associations.tolist(),
                "text_tail_associations": text_tail_associations.tolist(),
                "character_cluster_labels": character_cluster_labels,
                "is_essential_text": is_essential_text.tolist(),
            })

        return results

    def get_affinity_matrices_given_annotations(
            self, images, annotations, move_to_device_fn=None, apply_sigmoid=True
    ):
        assert not self.config.disable_detections
        move_to_device_fn = self.move_to_device if move_to_device_fn is None else move_to_device_fn

        character_bboxes_in_batch = [[bbox for bbox, label in zip(a["bboxes_as_x1y1x2y2"], a["labels"]) if label == 0] for a in annotations]
        crop_embeddings_for_batch = self.predict_crop_embeddings(images, character_bboxes_in_batch, move_to_device_fn)

        inputs_to_detection_transformer = self.processor.preprocess_inputs_for_detection(images, annotations)
        inputs_to_detection_transformer = move_to_device_fn(inputs_to_detection_transformer)
        processed_targets = inputs_to_detection_transformer.pop("labels")

        detection_transformer_output = self._get_detection_transformer_output(**inputs_to_detection_transformer)
        predicted_obj_tokens_for_batch = self._get_predicted_obj_tokens(detection_transformer_output)
        predicted_t2c_tokens_for_batch = self._get_predicted_t2c_tokens(detection_transformer_output)
        predicted_c2c_tokens_for_batch = self._get_predicted_c2c_tokens(detection_transformer_output)

        predicted_class_scores, predicted_bboxes = self._get_predicted_bboxes_and_classes(detection_transformer_output)
        matching_dict = {
            "logits": predicted_class_scores,
            "pred_boxes": predicted_bboxes,
        }
        indices = self.matcher(matching_dict, processed_targets)

        matched_char_obj_tokens_for_batch = []
        matched_text_obj_tokens_for_batch = []
        matched_tail_obj_tokens_for_batch = []
        t2c_tokens_for_batch = []
        c2c_tokens_for_batch = []

        for j, (pred_idx, tgt_idx) in enumerate(indices):
            target_idx_to_pred_idx = {tgt.item(): pred.item() for pred, tgt in zip(pred_idx, tgt_idx)}
            targets_for_this_image = processed_targets[j]
            indices_of_text_boxes_in_annotation = [i for i, label in enumerate(targets_for_this_image["class_labels"]) if label == 1]
            indices_of_char_boxes_in_annotation = [i for i, label in enumerate(targets_for_this_image["class_labels"]) if label == 0]
            indices_of_tail_boxes_in_annotation = [i for i, label in enumerate(targets_for_this_image["class_labels"]) if label == 3]
            predicted_text_indices = [target_idx_to_pred_idx[i] for i in indices_of_text_boxes_in_annotation]
            predicted_char_indices = [target_idx_to_pred_idx[i] for i in indices_of_char_boxes_in_annotation]
            predicted_tail_indices = [target_idx_to_pred_idx[i] for i in indices_of_tail_boxes_in_annotation]
            matched_char_obj_tokens_for_batch.append(predicted_obj_tokens_for_batch[j][predicted_char_indices])
            matched_text_obj_tokens_for_batch.append(predicted_obj_tokens_for_batch[j][predicted_text_indices])
            matched_tail_obj_tokens_for_batch.append(predicted_obj_tokens_for_batch[j][predicted_tail_indices])
            t2c_tokens_for_batch.append(predicted_t2c_tokens_for_batch[j])
            c2c_tokens_for_batch.append(predicted_c2c_tokens_for_batch[j])
        
        text_character_affinity_matrices = self._get_text_character_affinity_matrices(
            character_obj_tokens_for_batch=matched_char_obj_tokens_for_batch,
            text_obj_tokens_for_this_batch=matched_text_obj_tokens_for_batch,
            t2c_tokens_for_batch=t2c_tokens_for_batch,
            apply_sigmoid=apply_sigmoid,
        )

        character_character_affinity_matrices = self._get_character_character_affinity_matrices(
            character_obj_tokens_for_batch=matched_char_obj_tokens_for_batch,
            crop_embeddings_for_batch=crop_embeddings_for_batch,
            c2c_tokens_for_batch=c2c_tokens_for_batch,
            apply_sigmoid=apply_sigmoid,
        )
        
        character_character_affinity_matrices_crop_only = self._get_character_character_affinity_matrices(
            character_obj_tokens_for_batch=matched_char_obj_tokens_for_batch,
            crop_embeddings_for_batch=crop_embeddings_for_batch,
            c2c_tokens_for_batch=c2c_tokens_for_batch,
            crop_only=True,
            apply_sigmoid=apply_sigmoid,
        )

        text_tail_affinity_matrices = self._get_text_tail_affinity_matrices(
            text_obj_tokens_for_this_batch=matched_text_obj_tokens_for_batch,
            tail_obj_tokens_for_batch=matched_tail_obj_tokens_for_batch,
            apply_sigmoid=apply_sigmoid,
        )

        is_this_text_a_dialogue = self._get_text_classification(matched_text_obj_tokens_for_batch, apply_sigmoid=apply_sigmoid)

        return {
            "text_character_affinity_matrices": text_character_affinity_matrices,
            "character_character_affinity_matrices": character_character_affinity_matrices,
            "character_character_affinity_matrices_crop_only": character_character_affinity_matrices_crop_only,
            "text_tail_affinity_matrices": text_tail_affinity_matrices,
            "is_this_text_a_dialogue": is_this_text_a_dialogue,
        }

    
    def predict_crop_embeddings(self, images, crop_bboxes, move_to_device_fn=None, mask_ratio=0.0, batch_size=256):
        if self.config.disable_crop_embeddings:
            return None
        
        assert isinstance(crop_bboxes, List), "please provide a list of bboxes for each image to get embeddings for"
        
        move_to_device_fn = self.move_to_device if move_to_device_fn is None else move_to_device_fn
        
        # temporarily change the mask ratio from default to the one specified
        old_mask_ratio = self.crop_embedding_model.embeddings.config.mask_ratio
        self.crop_embedding_model.embeddings.config.mask_ratio = mask_ratio

        crops_per_image = []
        num_crops_per_batch = [len(bboxes) for bboxes in crop_bboxes]
        for image, bboxes, num_crops in zip(images, crop_bboxes, num_crops_per_batch):
            crops = self.processor.crop_image(image, bboxes)
            assert len(crops) == num_crops
            crops_per_image.extend(crops)
        
        if len(crops_per_image) == 0:
            return [move_to_device_fn(torch.zeros(0, self.config.crop_embedding_model_config.hidden_size)) for _ in crop_bboxes]

        crops_per_image = self.processor.preprocess_inputs_for_crop_embeddings(crops_per_image)
        crops_per_image = move_to_device_fn(crops_per_image)
        
        # process the crops in batches to avoid OOM
        embeddings = []
        for i in range(0, len(crops_per_image), batch_size):
            crops = crops_per_image[i:i+batch_size]
            embeddings_per_batch = self.crop_embedding_model(crops).last_hidden_state[:, 0]
            embeddings.append(embeddings_per_batch)
        embeddings = torch.cat(embeddings, dim=0)

        crop_embeddings_for_batch = []
        for num_crops in num_crops_per_batch:
            crop_embeddings_for_batch.append(embeddings[:num_crops])
            embeddings = embeddings[num_crops:]
        
        # restore the mask ratio to the default
        self.crop_embedding_model.embeddings.config.mask_ratio = old_mask_ratio

        return crop_embeddings_for_batch
    
    def predict_ocr(self, images, crop_bboxes, move_to_device_fn=None, use_tqdm=False, batch_size=32, max_new_tokens=64):
        assert not self.config.disable_ocr
        move_to_device_fn = self.move_to_device if move_to_device_fn is None else move_to_device_fn

        crops_per_image = []
        num_crops_per_batch = [len(bboxes) for bboxes in crop_bboxes]
        for image, bboxes, num_crops in zip(images, crop_bboxes, num_crops_per_batch):
            crops = self.processor.crop_image(image, bboxes)
            assert len(crops) == num_crops
            crops_per_image.extend(crops)
        
        if len(crops_per_image) == 0:
            return [[] for _ in crop_bboxes]

        crops_per_image = self.processor.preprocess_inputs_for_ocr(crops_per_image)
        crops_per_image = move_to_device_fn(crops_per_image)
        
        # process the crops in batches to avoid OOM
        all_generated_texts = []
        if use_tqdm:
            from tqdm import tqdm
            pbar = tqdm(range(0, len(crops_per_image), batch_size))
        else:
            pbar = range(0, len(crops_per_image), batch_size)
        for i in pbar:
            crops = crops_per_image[i:i+batch_size]
            generated_ids = self.ocr_model.generate(crops, max_new_tokens=max_new_tokens)
            generated_texts = self.processor.postprocess_ocr_tokens(generated_ids)
            all_generated_texts.extend(generated_texts)

        texts_for_images = []
        for num_crops in num_crops_per_batch:
            texts_for_images.append([x.replace("\n", "") for x in all_generated_texts[:num_crops]])
            all_generated_texts = all_generated_texts[num_crops:]

        return texts_for_images
    
    def visualise_single_image_prediction(
            self, image_as_np_array, predictions, filename=None
    ):
        return visualise_single_image_prediction(image_as_np_array, predictions, filename)

    
    @torch.no_grad()
    def _get_detection_transformer_output(
            self, 
            pixel_values: torch.FloatTensor,
            pixel_mask: Optional[torch.LongTensor] = None
    ):
        if self.config.disable_detections:
            raise ValueError("Detection model is disabled. Set disable_detections=False in the config.")
        return self.detection_transformer(
            pixel_values=pixel_values,
            pixel_mask=pixel_mask,
            return_dict=True
        )
    
    def _get_predicted_obj_tokens(
            self,
            detection_transformer_output: ConditionalDetrModelOutput
    ):
        return detection_transformer_output.last_hidden_state[:, :-self.num_non_obj_tokens]
    
    def _get_predicted_c2c_tokens(
            self,
            detection_transformer_output: ConditionalDetrModelOutput
    ):
        return detection_transformer_output.last_hidden_state[:, -self.num_non_obj_tokens]
    
    def _get_predicted_t2c_tokens(
            self,
            detection_transformer_output: ConditionalDetrModelOutput
    ):
        return detection_transformer_output.last_hidden_state[:, -self.num_non_obj_tokens+1]
    
    def _get_predicted_bboxes_and_classes(
            self,
            detection_transformer_output: ConditionalDetrModelOutput,
    ):
        if self.config.disable_detections:
            raise ValueError("Detection model is disabled. Set disable_detections=False in the config.")

        obj = self._get_predicted_obj_tokens(detection_transformer_output)

        predicted_class_scores = self.class_labels_classifier(obj)
        reference = detection_transformer_output.reference_points[:-self.num_non_obj_tokens] 
        reference_before_sigmoid = inverse_sigmoid(reference).transpose(0, 1)
        predicted_boxes = self.bbox_predictor(obj)
        predicted_boxes[..., :2] += reference_before_sigmoid
        predicted_boxes = predicted_boxes.sigmoid()

        return predicted_class_scores, predicted_boxes
    
    def _get_text_classification(
            self,
            text_obj_tokens_for_batch: List[torch.FloatTensor],
            apply_sigmoid=False,
    ):
        assert not self.config.disable_detections
        is_this_text_a_dialogue = []
        for text_obj_tokens in text_obj_tokens_for_batch:
            if text_obj_tokens.shape[0] == 0:
                is_this_text_a_dialogue.append(torch.tensor([], dtype=torch.bool))
                continue
            classification = self.is_this_text_a_dialogue(text_obj_tokens).squeeze(-1)
            if apply_sigmoid:
                classification = classification.sigmoid()
            is_this_text_a_dialogue.append(classification)
        return is_this_text_a_dialogue
    
    def _get_character_character_affinity_matrices(
            self,
            character_obj_tokens_for_batch: List[torch.FloatTensor] = None,
            crop_embeddings_for_batch: List[torch.FloatTensor] = None,
            c2c_tokens_for_batch: List[torch.FloatTensor] = None,
            crop_only=False,
            apply_sigmoid=True,
    ):
        assert self.config.disable_detections or (character_obj_tokens_for_batch is not None and c2c_tokens_for_batch is not None)
        assert self.config.disable_crop_embeddings or crop_embeddings_for_batch is not None
        assert not self.config.disable_detections or not self.config.disable_crop_embeddings

        if crop_only:
            affinity_matrices = []
            for crop_embeddings in crop_embeddings_for_batch:
                crop_embeddings = crop_embeddings / crop_embeddings.norm(dim=-1, keepdim=True)
                affinity_matrix = crop_embeddings @ crop_embeddings.T
                affinity_matrices.append(affinity_matrix)
            return affinity_matrices
        affinity_matrices = []
        for batch_index, (character_obj_tokens, c2c) in enumerate(zip(character_obj_tokens_for_batch, c2c_tokens_for_batch)):
            if character_obj_tokens.shape[0] == 0:
                affinity_matrices.append(torch.zeros(0, 0).type_as(character_obj_tokens))
                continue
            if not self.config.disable_crop_embeddings:
                crop_embeddings = crop_embeddings_for_batch[batch_index]
                assert character_obj_tokens.shape[0] == crop_embeddings.shape[0]
                character_obj_tokens = torch.cat([character_obj_tokens, crop_embeddings], dim=-1)
            char_i = repeat(character_obj_tokens, "i d -> i repeat d", repeat=character_obj_tokens.shape[0])
            char_j = repeat(character_obj_tokens, "j d -> repeat j d", repeat=character_obj_tokens.shape[0])
            char_ij = rearrange([char_i, char_j], "two i j d -> (i j) (two d)")
            c2c = repeat(c2c, "d -> repeat d", repeat = char_ij.shape[0])
            char_ij_c2c = torch.cat([char_ij, c2c], dim=-1)
            character_character_affinities = self.character_character_matching_head(char_ij_c2c)
            character_character_affinities = rearrange(character_character_affinities, "(i j) 1 -> i j", i=char_i.shape[0])
            character_character_affinities = (character_character_affinities + character_character_affinities.T) / 2
            if apply_sigmoid:
                character_character_affinities = character_character_affinities.sigmoid()
            affinity_matrices.append(character_character_affinities)
        return affinity_matrices
    
    def _get_text_character_affinity_matrices(
            self,
            character_obj_tokens_for_batch: List[torch.FloatTensor] = None,
            text_obj_tokens_for_this_batch: List[torch.FloatTensor] = None,
            t2c_tokens_for_batch: List[torch.FloatTensor] = None,
            apply_sigmoid=True,
    ):
        assert not self.config.disable_detections
        assert character_obj_tokens_for_batch is not None and text_obj_tokens_for_this_batch is not None and t2c_tokens_for_batch is not None
        affinity_matrices = []
        for character_obj_tokens, text_obj_tokens, t2c in zip(character_obj_tokens_for_batch, text_obj_tokens_for_this_batch, t2c_tokens_for_batch):
            if character_obj_tokens.shape[0] == 0 or text_obj_tokens.shape[0] == 0:
                affinity_matrices.append(torch.zeros(text_obj_tokens.shape[0], character_obj_tokens.shape[0]).type_as(character_obj_tokens))
                continue
            text_i = repeat(text_obj_tokens, "i d -> i repeat d", repeat=character_obj_tokens.shape[0])
            char_j = repeat(character_obj_tokens, "j d -> repeat j d", repeat=text_obj_tokens.shape[0])
            text_char = rearrange([text_i, char_j], "two i j d -> (i j) (two d)")
            t2c = repeat(t2c, "d -> repeat d", repeat = text_char.shape[0])
            text_char_t2c = torch.cat([text_char, t2c], dim=-1)
            text_character_affinities = self.text_character_matching_head(text_char_t2c)
            text_character_affinities = rearrange(text_character_affinities, "(i j) 1 -> i j", i=text_i.shape[0])
            if apply_sigmoid:
                text_character_affinities = text_character_affinities.sigmoid()
            affinity_matrices.append(text_character_affinities)
        return affinity_matrices
    
    def _get_text_tail_affinity_matrices(
            self,
            text_obj_tokens_for_this_batch: List[torch.FloatTensor] = None,
            tail_obj_tokens_for_batch: List[torch.FloatTensor] = None,
            apply_sigmoid=True,
    ):
        assert not self.config.disable_detections
        assert tail_obj_tokens_for_batch is not None and text_obj_tokens_for_this_batch is not None
        affinity_matrices = []
        for tail_obj_tokens, text_obj_tokens in zip(tail_obj_tokens_for_batch, text_obj_tokens_for_this_batch):
            if tail_obj_tokens.shape[0] == 0 or text_obj_tokens.shape[0] == 0:
                affinity_matrices.append(torch.zeros(text_obj_tokens.shape[0], tail_obj_tokens.shape[0]).type_as(tail_obj_tokens))
                continue
            text_i = repeat(text_obj_tokens, "i d -> i repeat d", repeat=tail_obj_tokens.shape[0])
            tail_j = repeat(tail_obj_tokens, "j d -> repeat j d", repeat=text_obj_tokens.shape[0])
            text_tail = rearrange([text_i, tail_j], "two i j d -> (i j) (two d)")
            text_tail_affinities = self.text_tail_matching_head(text_tail)
            text_tail_affinities = rearrange(text_tail_affinities, "(i j) 1 -> i j", i=text_i.shape[0])
            if apply_sigmoid:
                text_tail_affinities = text_tail_affinities.sigmoid()
            affinity_matrices.append(text_tail_affinities)
        return affinity_matrices

# Copied from transformers.models.detr.modeling_detr._upcast
def _upcast(t):
    # Protects from numerical overflows in multiplications by upcasting to the equivalent higher type
    if t.is_floating_point():
        return t if t.dtype in (torch.float32, torch.float64) else t.float()
    else:
        return t if t.dtype in (torch.int32, torch.int64) else t.int()


# Copied from transformers.models.detr.modeling_detr.box_area
def box_area(boxes):
    """
    Computes the area of a set of bounding boxes, which are specified by its (x1, y1, x2, y2) coordinates.

    Args:
        boxes (`torch.FloatTensor` of shape `(number_of_boxes, 4)`):
            Boxes for which the area will be computed. They are expected to be in (x1, y1, x2, y2) format with `0 <= x1
            < x2` and `0 <= y1 < y2`.

    Returns:
        `torch.FloatTensor`: a tensor containing the area for each box.
    """
    boxes = _upcast(boxes)
    return (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])


# Copied from transformers.models.detr.modeling_detr.box_iou
def box_iou(boxes1, boxes2):
    area1 = box_area(boxes1)
    area2 = box_area(boxes2)

    left_top = torch.max(boxes1[:, None, :2], boxes2[:, :2])  # [N,M,2]
    right_bottom = torch.min(boxes1[:, None, 2:], boxes2[:, 2:])  # [N,M,2]

    width_height = (right_bottom - left_top).clamp(min=0)  # [N,M,2]
    inter = width_height[:, :, 0] * width_height[:, :, 1]  # [N,M]

    union = area1[:, None] + area2 - inter

    iou = inter / union
    return iou, union


# Copied from transformers.models.detr.modeling_detr.generalized_box_iou
def generalized_box_iou(boxes1, boxes2):
    """
    Generalized IoU from https://giou.stanford.edu/. The boxes should be in [x0, y0, x1, y1] (corner) format.

    Returns:
        `torch.FloatTensor`: a [N, M] pairwise matrix, where N = len(boxes1) and M = len(boxes2)
    """
    # degenerate boxes gives inf / nan results
    # so do an early check
    if not (boxes1[:, 2:] >= boxes1[:, :2]).all():
        raise ValueError(f"boxes1 must be in [x0, y0, x1, y1] (corner) format, but got {boxes1}")
    if not (boxes2[:, 2:] >= boxes2[:, :2]).all():
        raise ValueError(f"boxes2 must be in [x0, y0, x1, y1] (corner) format, but got {boxes2}")
    iou, union = box_iou(boxes1, boxes2)

    top_left = torch.min(boxes1[:, None, :2], boxes2[:, :2])
    bottom_right = torch.max(boxes1[:, None, 2:], boxes2[:, 2:])

    width_height = (bottom_right - top_left).clamp(min=0)  # [N,M,2]
    area = width_height[:, :, 0] * width_height[:, :, 1]

    return iou - (area - union) / area


# Copied from transformers.models.deformable_detr.modeling_deformable_detr.DeformableDetrHungarianMatcher with DeformableDetr->ConditionalDetr
class ConditionalDetrHungarianMatcher(nn.Module):
    """
    This class computes an assignment between the targets and the predictions of the network.

    For efficiency reasons, the targets don't include the no_object. Because of this, in general, there are more
    predictions than targets. In this case, we do a 1-to-1 matching of the best predictions, while the others are
    un-matched (and thus treated as non-objects).

    Args:
        class_cost:
            The relative weight of the classification error in the matching cost.
        bbox_cost:
            The relative weight of the L1 error of the bounding box coordinates in the matching cost.
        giou_cost:
            The relative weight of the giou loss of the bounding box in the matching cost.
    """

    def __init__(self, class_cost: float = 1, bbox_cost: float = 1, giou_cost: float = 1):
        super().__init__()

        self.class_cost = class_cost
        self.bbox_cost = bbox_cost
        self.giou_cost = giou_cost
        if class_cost == 0 and bbox_cost == 0 and giou_cost == 0:
            raise ValueError("All costs of the Matcher can't be 0")

    @torch.no_grad()
    def forward(self, outputs, targets):
        """
        Args:
            outputs (`dict`):
                A dictionary that contains at least these entries:
                * "logits": Tensor of dim [batch_size, num_queries, num_classes] with the classification logits
                * "pred_boxes": Tensor of dim [batch_size, num_queries, 4] with the predicted box coordinates.
            targets (`List[dict]`):
                A list of targets (len(targets) = batch_size), where each target is a dict containing:
                * "class_labels": Tensor of dim [num_target_boxes] (where num_target_boxes is the number of
                  ground-truth
                 objects in the target) containing the class labels
                * "boxes": Tensor of dim [num_target_boxes, 4] containing the target box coordinates.

        Returns:
            `List[Tuple]`: A list of size `batch_size`, containing tuples of (index_i, index_j) where:
            - index_i is the indices of the selected predictions (in order)
            - index_j is the indices of the corresponding selected targets (in order)
            For each batch element, it holds: len(index_i) = len(index_j) = min(num_queries, num_target_boxes)
        """
        batch_size, num_queries = outputs["logits"].shape[:2]

        # We flatten to compute the cost matrices in a batch
        out_prob = outputs["logits"].flatten(0, 1).sigmoid()  # [batch_size * num_queries, num_classes]
        out_bbox = outputs["pred_boxes"].flatten(0, 1)  # [batch_size * num_queries, 4]

        # Also concat the target labels and boxes
        target_ids = torch.cat([v["class_labels"] for v in targets])
        target_bbox = torch.cat([v["boxes"] for v in targets])

        # Compute the classification cost.
        alpha = 0.25
        gamma = 2.0
        neg_cost_class = (1 - alpha) * (out_prob**gamma) * (-(1 - out_prob + 1e-8).log())
        pos_cost_class = alpha * ((1 - out_prob) ** gamma) * (-(out_prob + 1e-8).log())
        class_cost = pos_cost_class[:, target_ids] - neg_cost_class[:, target_ids]

        # Compute the L1 cost between boxes
        bbox_cost = torch.cdist(out_bbox, target_bbox, p=1)

        # Compute the giou cost between boxes
        giou_cost = -generalized_box_iou(center_to_corners_format(out_bbox), center_to_corners_format(target_bbox))

        # Final cost matrix
        cost_matrix = self.bbox_cost * bbox_cost + self.class_cost * class_cost + self.giou_cost * giou_cost
        cost_matrix = cost_matrix.view(batch_size, num_queries, -1).cpu()

        sizes = [len(v["boxes"]) for v in targets]
        indices = [linear_sum_assignment(c[i]) for i, c in enumerate(cost_matrix.split(sizes, -1))]
        return [(torch.as_tensor(i, dtype=torch.int64), torch.as_tensor(j, dtype=torch.int64)) for i, j in indices]
