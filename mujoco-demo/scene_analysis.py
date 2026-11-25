"""Describe a CCTV frame with a VLM and flag a possible fallen worker (torch is imported lazily).

The real-time loop keeps using the person detector. This runs only on selected frames (e.g. after a
HazardEvent) because caption generation is slow. Two independent cues are recorded so each can be
scored once labelled fall footage exists:

- ``caption_terms``: fall wording found in the VLM caption
- ``lying_boxes``: person boxes that are wider than tall

Either cue sets ``fall_suspected``; a missed fall costs more than a false alarm. Neither cue has been
validated on fall footage, so the result is evidence for a reviewer, not an approved HazardEvent.

Run inside a GPU allocation:

    python scene_analysis.py frame_a.png frame_b.png --output vlm_results/scene_analysis.jsonl
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

CAPTION_TASK = "<MORE_DETAILED_CAPTION>"
# 캡션은 영어로 생성된다. 단어 경계로 찾아 'floor'만 있는 문장은 걸리지 않게 한다.
FALL_PATTERNS = (
    r"\b(?:lying|laying|lies|lay)\b(?:\s+\w+){0,3}\s+(?:on|upon)\s+(?:the\s+)?(?:floor|ground)",
    r"\b(?:fallen|fell|falling down|collapsed|unconscious|passed out)\b",
    r"\b(?:face ?down|on (?:his|her|their) (?:back|side|stomach))\b",
)
# 서 있는 사람의 상자는 세로가 길다. 이 비율부터 누운 자세 후보로 본다(검증 전 초기값).
LYING_ASPECT_RATIO = 1.2


def caption_fall_terms(caption):
    """Fall wording in the caption, in order of appearance. Negation is not handled."""
    found = []
    for pattern in FALL_PATTERNS:
        found.extend((match.start(), match.group(0)) for match in re.finditer(pattern, caption.lower()))
    return [term for _, term in sorted(found)]


def lying_boxes(boxes, ratio=LYING_ASPECT_RATIO):
    """Indexes of ``[x1, y1, x2, y2]`` boxes at least ``ratio`` times wider than tall."""
    result = []
    for index, (x1, y1, x2, y2) in enumerate(boxes):
        width, height = x2 - x1, y2 - y1
        if width > 0 and height > 0 and width / height >= ratio:
            result.append(index)
    return result


def analysis_result(sensor_id, image, caption, people, model, generation_ms,
                    run_id=None, hazard_id=None, now=None):
    """One analysis record. ``people`` is the detector's ``[{"bbox_xyxy": [...], ...}]`` list."""
    terms = caption_fall_terms(caption)
    lying = lying_boxes([person["bbox_xyxy"] for person in people])
    return {
        "analysis_id": f"analysis-{uuid4()}",
        "run_id": run_id,
        "hazard_id": hazard_id,
        "sensor_id": sensor_id,
        "image": image,
        "analyzed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "model": model,
        "task": CAPTION_TASK,
        "caption": caption,
        "people": people,
        "caption_terms": terms,
        "lying_boxes": lying,
        "fall_suspected": bool(terms or lying),
        "generation_ms": generation_ms,
        "note": "Unvalidated cues for review; not an approved safety Observation or HazardEvent.",
    }


class FlorenceCaptioner:
    name = "florence2"

    def __init__(self, model_id, num_beams=3):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        self.model_id = model_id
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, trust_remote_code=True, torch_dtype=torch.float16,
            attn_implementation="eager",
        ).to("cuda").eval()
        self.processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        self.num_beams = num_beams

    def caption(self, image):
        """Caption text and the timed generation call in milliseconds."""
        import torch

        inputs = self.processor(text=CAPTION_TASK, images=image,
                                return_tensors="pt").to("cuda", torch.float16)
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            generated = self.model.generate(
                input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                max_new_tokens=256, do_sample=False, num_beams=self.num_beams,
            )
        torch.cuda.synchronize()
        generation_ms = (time.perf_counter() - started) * 1000
        raw = self.processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = self.processor.post_process_generation(raw, task=CAPTION_TASK, image_size=image.size)
        return str(parsed.get(CAPTION_TASK, "")).strip(), generation_ms


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--model", default="microsoft/Florence-2-base")
    parser.add_argument("--detector-model", default="PekingU/rtdetr_r50vd",
                        help="RT-DETR model for the person boxes")
    parser.add_argument("--sensor-id", help="default: the image file name up to the first '-'")
    parser.add_argument("--output", type=Path, default=Path("vlm_results/scene_analysis.jsonl"))
    args = parser.parse_args()
    for path in args.images:
        if not path.is_file():
            parser.error(f"Image not found: {path}")

    import torch
    from PIL import Image

    from camera_frames import person_detections
    from detectors import WORKER_SCORE_THRESHOLD, RtDetrDetector

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable. Run in a Slurm GPU allocation with CUDA PyTorch.")
    captioner = FlorenceCaptioner(args.model)
    detector = RtDetrDetector(args.detector_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    suspected = 0
    with args.output.open("w", encoding="utf-8") as file:
        for path in args.images:
            with Image.open(path) as source:
                image = source.convert("RGB")
            detection, _ = detector.detect(image)
            people = person_detections(detection, image.width, image.height, WORKER_SCORE_THRESHOLD)
            caption, generation_ms = captioner.caption(image)
            record = analysis_result(args.sensor_id or path.stem.split("-")[0], str(path), caption,
                                     people, args.model, generation_ms)
            suspected += record["fall_suspected"]
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{path.name}: fall_suspected={record['fall_suspected']} "
                  f"terms={record['caption_terms']} lying_boxes={record['lying_boxes']}", flush=True)
    print(f"{suspected}/{len(args.images)} frames flagged; results saved to {args.output.resolve()}")


if __name__ == "__main__":
    main()
