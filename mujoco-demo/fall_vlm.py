"""CCTV 영상에서 Florence-2로 바닥 통로에 누운 작업자를 찾는다."""
import argparse
import json
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

from hazard_reporter import camera_from_pose, point_in_ring
from observation_export import ground_position

DEFAULT_MODEL = "florence-community/Florence-2-base"
TASK = "<CAPTION_TO_PHRASE_GROUNDING>"
PROMPT = "A person in an orange safety vest lying on the floor."


def project_point(point, camera):
    """MuJoCo 카메라 좌표(-Z 전방, +Y 위)를 영상 픽셀로 투영한다."""
    width, height = camera["width"], camera["height"]
    focal = height / (2 * math.tan(math.radians(camera["fovy"]) / 2))
    delta = [a - b for a, b in zip(point, camera["pos"])]
    x, y, z = [sum(delta[row] * camera["mat"][row * 3 + col] for row in range(3)) for col in range(3)]
    if z >= 0:
        raise ValueError("관측 구역이 카메라 앞에 있지 않습니다.")
    return width / 2 + focal * x / -z, height / 2 - focal * y / -z


def zone_crop(camera, polygon):
    """검출에 필요한 지면 및 작업자 높이를 포함하는 영상 영역."""
    points = [project_point((x, y, z), camera) for x, y in polygon for z in (0.0, 1.05)]
    xs, ys = zip(*points)
    bounds = (max(0, math.floor(min(xs))), max(0, math.floor(min(ys))),
              min(camera["width"], math.ceil(max(xs))), min(camera["height"], math.ceil(max(ys))))
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError("카메라 영상에 관측 구역이 없습니다.")
    return bounds


def fallen_workers(prediction, camera, polygon, bounds):
    """누운 작업자 검출만 원본 좌표로 옮기고 검출 하단을 바닥 구역과 대조한다."""
    rotation = [camera["mat"][row:row + 3] for row in range(0, 9, 3)]
    projection = camera_from_pose(camera["pos"], rotation, camera["fovy"])
    ring = [*polygon, polygon[0]]
    left, top, right, bottom = bounds
    result = []
    for label, bbox in zip(prediction.get("labels", []), prediction.get("bboxes", [])):
        if not any(word in label.casefold() for word in ("person", "worker", "man", "human")) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in bbox)
        if not (0 <= x1 < x2 <= right - left and 0 <= y1 < y2 <= bottom - top):
            continue
        # CCTV 04에서 넘어져 가로로 누운 자세를 확인한다.
        if (x2 - x1) < 1.35 * (y2 - y1):
            continue
        full_box = [x1 + left, y1 + top, x2 + left, y2 + top]
        try:
            ground = ground_position(full_box, camera["width"], camera["height"], projection)
        except ValueError:
            continue
        if point_in_ring(ground["x"], ground["y"], ring):
            result.append({"bbox_xyxy": full_box, "label": label, "ground": ground})
    return result


class FlorenceFallDetector:
    def __init__(self, model_id, device):
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration

        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.device = device
        self.model = Florence2ForConditionalGeneration.from_pretrained(model_id).to(device).eval()

    def detect(self, image):
        inputs = self.processor(text=TASK + PROMPT, images=image, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            tokens = self.model.generate(**inputs, max_new_tokens=128, num_beams=1)
        generated = self.processor.batch_decode(tokens, skip_special_tokens=False)[0]
        return self.processor.post_process_generation(generated, task=TASK, image_size=image.size)[TASK]


def analyze(paths, camera, polygon, detector):
    bounds = zone_crop(camera, polygon)
    results = []
    for path in paths:
        with Image.open(path) as source:
            image = source.convert("RGB")
        if image.size != (camera["width"], camera["height"]):
            raise ValueError("카메라 보정값과 입력 영상 크기가 다릅니다.")
        boxes = fallen_workers(detector.detect(image.crop(bounds)), camera, polygon, bounds)
        results.append(boxes)
    return results


def write_preview(image_path, boxes, polygon, camera, output):
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    points = [project_point((x, y, 0.0), camera) for x, y in polygon]
    draw.line([*points, points[0]], fill="#ffe070", width=2)
    for item in boxes:
        x, y, _, _ = item["bbox_xyxy"]
        draw.rectangle(item["bbox_xyxy"], outline="#ff674d", width=3)
        draw.text((x + 2, max(0, y - 14)), "FLORENCE-2: PERSON DOWN", fill="#ffdd91")
    image.save(output, quality=92)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", nargs="+", type=Path)
    parser.add_argument("--camera", type=Path, required=True)
    parser.add_argument("--polygon", type=json.loads, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--model", default=os.environ.get("WAREHOUSE_FALL_MODEL", DEFAULT_MODEL))
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default=os.environ.get("WAREHOUSE_FALL_DEVICE", "cpu"))
    args = parser.parse_args()
    camera = json.loads(args.camera.read_text())["cameras"]["cctv_4"]
    observations = analyze(args.frames, camera, args.polygon, FlorenceFallDetector(args.model, args.device))
    best = max(range(len(observations)), key=lambda i: (len(observations[i]), i))
    write_preview(args.frames[best], observations[best], args.polygon, camera, args.preview)
    print(json.dumps({"model": args.model, "frames": observations}))


if __name__ == "__main__":
    main()
