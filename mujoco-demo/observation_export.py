"""Convert scored CCTV detections to ontology Observations and hold unscored ones."""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def ground_position(box, width, height, camera):
    """Intersect the ray through a bounding box's bottom centre with the floor."""
    x1, y1, x2, y2 = (float(value) for value in box)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f"Invalid pixel box: {box}")

    fovy = float(camera["fovy_deg"])
    if not 0 < fovy < 180:
        raise ValueError("Camera fovy_deg must be between 0 and 180")
    focal_px = height / (2 * math.tan(math.radians(fovy) / 2))
    local_ray = ((x1 + x2 - width) / (2 * focal_px),
                 (height / 2 - y2) / focal_px, -1.0)
    rotation = camera["rotation_cam_to_world"]
    origin = [float(value) for value in camera["position_m"]]
    if len(origin) != 3 or len(rotation) != 3 or any(len(row) != 3 for row in rotation):
        raise ValueError("Camera position or rotation has an invalid shape")
    ray = [sum(float(rotation[row][col]) * local_ray[col] for col in range(3))
           for row in range(3)]
    floor_z = float(camera["ground_plane_z_m"])
    if not all(math.isfinite(v) for v in origin + ray + [floor_z]) or ray[2] >= -1e-9:
        raise ValueError("Ray does not point toward a finite ground position")
    distance = (floor_z - origin[2]) / ray[2]
    if distance <= 0:
        raise ValueError("Ground plane is behind the camera")
    return {"x": origin[0] + distance * ray[0],
            "y": origin[1] + distance * ray[1], "z": floor_z}


def capture_index(metadata_path):
    captures = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(captures, list):
        raise ValueError("Capture metadata must be a list")
    indexed = {}
    for capture in captures:
        name = Path(capture["image"]).name
        if name in indexed:
            raise ValueError(f"Duplicate capture image: {name}")
        indexed[name] = capture
    return indexed


def export(detection_paths, metadata_path):
    captures = capture_index(metadata_path)
    observations, pending_confidence, skipped, details = [], [], [], []
    for result_path in detection_paths:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        image_name = Path(result["image"]).name
        if image_name not in captures:
            raise ValueError(f"No capture metadata for {image_name}")
        capture = captures[image_name]
        if not result.get("image_sha256") or not capture.get("image_sha256"):
            raise ValueError(f"Missing image SHA-256 for {image_name}; recapture and rerun detection")
        if result["image_sha256"] != capture["image_sha256"]:
            raise ValueError(f"Capture and detection refer to different image bytes: {image_name}")
        timestamp = datetime.fromisoformat(capture["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"Capture timestamp has no UTC offset: {image_name}")
        if capture["frame_id"] != "world":
            raise ValueError(f"Expected world frame for calibrated camera: {image_name}")
        if "camera" not in capture:
            raise ValueError(f"Missing camera calibration for {image_name}; rerun capture.py")
        if result.get("task") != "<OD>":
            raise ValueError(f"Expected Florence <OD> result: {result_path}")
        detection = result["detections"]["<OD>"]
        boxes, labels = detection["bboxes"], detection["labels"]
        scores = detection.get("scores")
        if len(boxes) != len(labels) or (scores is not None and len(scores) != len(boxes)):
            raise ValueError(f"Detection arrays have different lengths: {result_path}")

        for index, (box, label) in enumerate(zip(boxes, labels)):
            if str(label).casefold() != "person":
                skipped.append({"image": image_name, "detection_index": index,
                                "reason": f"Unmapped label: {label}"})
                continue
            try:
                position = ground_position(box, int(capture["width"]),
                                           int(capture["height"]), capture["camera"])
            except (KeyError, TypeError, ValueError) as error:
                skipped.append({"image": image_name, "detection_index": index,
                                "reason": f"Projection failed: {error}"})
                continue
            observation_key = (f"{capture['sensor_id']}:{timestamp.isoformat()}:"
                               f"{capture['image_sha256']}:{index}")
            observation_id = f"obs-{uuid5(NAMESPACE_URL, observation_key)}"
            observation = {
                "observation_id": observation_id,
                "sensor_id": capture["sensor_id"],
                "timestamp": timestamp.isoformat(),
                "detected_type": "WORKER",
                "position": position,
                "frame_id": "world",
                "track_id": None,
            }
            details.append({"observation_id": observation_id, "image": image_name,
                            "bbox_xywh": [float(box[0]), float(box[1]),
                                          float(box[2]) - float(box[0]),
                                          float(box[3]) - float(box[1])],
                            "position_source": "WORLD",
                            "position_method": "bounding-box bottom centre projected onto z=0"})
            if scores is None:
                pending_confidence.append({"candidate": observation,
                                           "reason": "Florence-2 <OD> returned no confidence score"})
                continue
            score = scores[index]
            if isinstance(score, bool) or not isinstance(score, (float, int)):
                raise ValueError(f"Invalid detection score for {image_name} index {index}")
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"Detection score outside [0, 1] for {image_name} index {index}")
            observation["confidence"] = float(score)
            observations.append(observation)

    return {"observations": observations,
            "pending_confidence": pending_confidence,
            "observation_details": details,
            "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("detections", nargs="+", type=Path, help="Florence result JSON files")
    parser.add_argument("--metadata", required=True, type=Path, help="capture.py metadata.json")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON file")
    args = parser.parse_args()
    try:
        data = export(args.detections, args.metadata)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(data['observations'])} Observations, "
          f"{len(data['pending_confidence'])} awaiting confidence to {args.output}")


if __name__ == "__main__":
    main()
