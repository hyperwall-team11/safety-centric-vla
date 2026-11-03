"""Interchangeable person detectors for the ROS camera subscriber (torch is imported lazily for CPU tests).

Each detector returns ``{"bboxes": [[x1, y1, x2, y2], ...], "labels": [...], "scores": [...] or None}``
in pixel coordinates, plus the timed model call in milliseconds. Florence-2 ``<OD>`` has no scores.
"""

import time

# 개발계획서 5-2: WORKER 신뢰도 임계 0.5. 이보다 낮은 탐지는 기록만 하고 작업자로 세지 않는다.
WORKER_SCORE_THRESHOLD = 0.5
# 미탐지 원인 분석용으로 기록하는 최소 점수. 작업자 판정에는 쓰지 않는다.
RECORD_SCORE_THRESHOLD = 0.3


def rtdetr_detection(result, id2label, width, height):
    """Convert one RT-DETR post-processed result to the common detection dict."""
    bboxes, labels, scores = [], [], []
    for score, label_id, box in zip(result["scores"].tolist(), result["labels"].tolist(),
                                    result["boxes"].tolist()):
        x1, y1, x2, y2 = (min(max(float(v), 0.0), limit)
                          for v, limit in zip(box, (width, height, width, height)))
        if x2 <= x1 or y2 <= y1:
            continue
        bboxes.append([x1, y1, x2, y2])
        labels.append(id2label.get(int(label_id), str(label_id)))
        scores.append(float(score))
    return {"bboxes": bboxes, "labels": labels, "scores": scores}


class FlorenceDetector:
    name = "florence2"
    task = "<OD>"

    def __init__(self, model_id):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=torch.float16,
            attn_implementation="eager",
        ).to("cuda").eval()
        self.processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)

    def detect(self, image):
        from florence_detect import detect_once

        _, parsed, inference_ms = detect_once(self.model, self.processor, image, self.task)
        detection = parsed.get(self.task, {})
        return {"bboxes": detection.get("bboxes", []), "labels": detection.get("labels", []),
                "scores": detection.get("scores")}, inference_ms


class RtDetrDetector:
    name = "rtdetr"
    task = "object_detection"

    def __init__(self, model_id, record_threshold=RECORD_SCORE_THRESHOLD):
        from transformers import AutoImageProcessor, RTDetrForObjectDetection

        self.model = RTDetrForObjectDetection.from_pretrained(model_id).to("cuda").eval()
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.record_threshold = record_threshold

    def detect(self, image):
        import torch

        inputs = self.processor(images=image, return_tensors="pt").to("cuda")
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            outputs = self.model(**inputs)
        torch.cuda.synchronize()
        inference_ms = (time.perf_counter() - started) * 1000
        result = self.processor.post_process_object_detection(
            outputs, target_sizes=[(image.height, image.width)], threshold=self.record_threshold)[0]
        return rtdetr_detection(result, self.model.config.id2label, image.width, image.height), inference_ms


DETECTORS = {"florence2": FlorenceDetector, "rtdetr": RtDetrDetector}
