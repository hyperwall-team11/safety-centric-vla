"""Measure person detection on offline virtual-CCTV renders against their ground truth.

The render folder is the output of ``control/scripts/threejs_cctv`` (PR #45):

    cctv_1/000000.png ...   camera images, frame number = file name
    frames.csv              per frame and worker: sim_time, x, y, in_keep_out (MuJoCo world, m)
    boxes.json              per frame, camera and worker: visible pixel box (pixels=0: not visible)
    cameras.json            camera pose (cam_xmat), fovy, fx/fy/cx/cy and the keep-out rectangle

Ground truth is used only for scoring; the detector sees the PNG and the camera calibration only.

    python offline_eval.py --render-dir ../control/outputs/threejs_cctv --detector florence2 \
        --model-id /abr/coss38/florence-2-base --output-dir eval/florence2

Writes ``frames.jsonl`` (one line per frame) and ``summary.json`` to ``--output-dir``.
``--detector truth`` feeds the ground-truth boxes as detections (no model, no GPU) to check that the
camera calibration and keep-out zone line up before a real run.
"""

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

from camera_calib import normalize_camera
from camera_frames import person_detections
from hazard_reporter import point_in_ring
from observation_export import ground_position

# 예측 상자와 정답(보이는 영역) 상자가 이만큼 겹치면 같은 작업자를 찾은 것으로 본다.
MATCH_IOU = 0.5
# 이보다 적게 보이는 작업자는 탐지 대상에서 뺀다(가장자리에 손만 걸친 경우 등). 빠진 수는 따로 센다.
MIN_VISIBLE_PIXELS = 400


def camera_from_render(sensor_id, info):
    """Calibration entry for one camera of ``cameras.json`` (MuJoCo camera, z-up world)."""
    mat = [float(v) for v in info["mat"]]
    if len(mat) != 9:
        raise ValueError(f"{sensor_id}: mat needs 9 numbers")
    return normalize_camera({
        "sensor_id": sensor_id, "width": int(info["width"]), "height": int(info["height"]),
        "position_m": info["pos"], "rotation_cam_to_world": [mat[0:3], mat[3:6], mat[6:9]],
        "intrinsics": {k: info[k] for k in ("fx", "fy", "cx", "cy")},
        "rotation_convention": "mujoco_camera", "world_frame": "mujoco_z_up"})


def rect_ring(rect):
    """Closed ring for an axis-aligned zone ``[centre_x, centre_y, half_x, half_y]``."""
    cx, cy, hx, hy = (float(v) for v in rect)
    if hx <= 0 or hy <= 0:
        raise ValueError("Zone half sizes must be positive")
    return [(cx - hx, cy - hy), (cx + hx, cy - hy), (cx + hx, cy + hy), (cx - hx, cy + hy), (cx - hx, cy - hy)]


def load_render(render_dir):
    render_dir = Path(render_dir)
    meta = json.loads((render_dir / "cameras.json").read_text(encoding="utf-8"))
    cameras = {sensor: camera_from_render(sensor, info) for sensor, info in sorted(meta["cameras"].items())}
    truth = {}
    with (render_dir / "frames.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            truth.setdefault(int(row["frame"]), {})[row["worker_id"]] = {
                "sim_time": float(row["sim_time"]), "x": float(row["x"]), "y": float(row["y"]),
                "in_keep_out": row["in_keep_out"] == "1"}
    boxes = {}
    for entry in json.loads((render_dir / "boxes.json").read_text(encoding="utf-8")):
        boxes[(int(entry["frame"]), entry["camera"])] = entry["workers"]
    ring = rect_ring(meta["zone_rects"]["keep_out"])
    return {"dir": render_dir, "cameras": cameras, "truth": truth, "boxes": boxes, "keep_out": ring,
            "fps": float(meta.get("fps", 0) or 0)}


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match(predicted, expected, threshold=MATCH_IOU):
    """Greedy one-to-one matching by IoU: ``[(pred_index, expected_index, iou), ...]``."""
    pairs = sorted(((iou(p, e), i, j) for i, p in enumerate(predicted) for j, e in enumerate(expected)),
                   reverse=True)
    used_p, used_e, matches = set(), set(), []
    for value, i, j in pairs:
        if value < threshold:
            break
        if i in used_p or j in used_e:
            continue
        used_p.add(i)
        used_e.add(j)
        matches.append((i, j, value))
    return matches


def visible_workers(worker_boxes, min_pixels=MIN_VISIBLE_PIXELS):
    """Worker id -> visible box ``[x0, y0, x1 + 1, y1 + 1]`` and the ids seen but too small."""
    visible, small = {}, []
    for box in worker_boxes:
        if not box.get("pixels"):
            continue
        if box["pixels"] < min_pixels:
            small.append(box["worker_id"])
            continue
        visible[box["worker_id"]] = [float(box["x0"]), float(box["y0"]), float(box["x1"]) + 1, float(box["y1"]) + 1]
    return visible, small


def score_frame(frame, people_by_camera, render, min_pixels=MIN_VISIBLE_PIXELS):
    """Compare one frame's person boxes with ground truth on every camera."""
    truth = render["truth"][frame]
    record = {"frame": frame, "sim_time": next(iter(truth.values()))["sim_time"],
              "inside_truth": sorted(w for w, t in truth.items() if t["in_keep_out"]), "cameras": {}}
    for sensor, people in people_by_camera.items():
        camera = render["cameras"][sensor]
        visible, small = visible_workers(render["boxes"].get((frame, sensor), []), min_pixels)
        ids = sorted(visible)
        predicted = [p["bbox_xyxy"] for p in people]
        pairs = match(predicted, [visible[w] for w in ids])
        found, errors = [], []
        for i, j, _ in pairs:
            worker = ids[j]
            found.append(worker)
            try:
                point = ground_position(predicted[i], camera["width"], camera["height"], camera)
            except ValueError:
                continue
            errors.append(((point["x"] - truth[worker]["x"]) ** 2 + (point["y"] - truth[worker]["y"]) ** 2) ** 0.5)
        inside = False
        for box in predicted:
            try:
                point = ground_position(box, camera["width"], camera["height"], camera)
            except ValueError:
                continue
            inside = inside or point_in_ring(point["x"], point["y"], render["keep_out"])
        record["cameras"][sensor] = {
            "visible": ids, "too_small": small, "found": sorted(found),
            "false_positives": len(predicted) - len(pairs), "position_errors_m": errors,
            "inside_detected": inside, "people": predicted}
    return record


def _rate(hit, total):
    return {"hit": hit, "total": total, "rate": round(hit / total, 4) if total else None}


def _stats(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {"count": len(ordered), "median": round(statistics.median(ordered), 4),
            "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 4),
            "max": round(ordered[-1], 4)}


def summarize(records):
    """Per-camera and combined (any camera) detection, keep-out alarm and position error."""
    sensors = sorted({s for r in records for s in r["cameras"]})
    summary = {"frames": len(records), "cameras": {}}
    for sensor in sensors:
        rows = [r for r in records if sensor in r["cameras"]]
        visible = sum(len(r["cameras"][sensor]["visible"]) for r in rows)
        found = sum(len(r["cameras"][sensor]["found"]) for r in rows)
        inside_visible = sum(len(set(r["cameras"][sensor]["visible"]) & set(r["inside_truth"])) for r in rows)
        inside_found = sum(len(set(r["cameras"][sensor]["found"]) & set(r["inside_truth"])) for r in rows)
        fp = sum(r["cameras"][sensor]["false_positives"] for r in rows)
        summary["cameras"][sensor] = {
            "recall": _rate(found, visible), "recall_in_keep_out": _rate(inside_found, inside_visible),
            "false_positives": fp, "precision": _rate(found, found + fp),
            "too_small": sum(len(r["cameras"][sensor]["too_small"]) for r in rows),
            "position_error_m": _stats([e for r in rows for e in r["cameras"][sensor]["position_errors_m"]])}
    # 결합: 어느 한 카메라라도 보이는 작업자를 찾으면 탐지로 본다.
    any_visible = any_found = 0
    for r in records:
        seen = set().union(*(set(c["visible"]) for c in r["cameras"].values()))
        hit = set().union(*(set(c["found"]) for c in r["cameras"].values()))
        any_visible += len(seen)
        any_found += len(hit & seen)
    summary["combined_recall"] = _rate(any_found, any_visible)
    # 금지구역 경보: 정답상 누군가 안에 있는 프레임에서 어느 카메라든 투영 위치가 구역 안이면 적중.
    intrusion = [r for r in records if r["inside_truth"]]
    clear = [r for r in records if not r["inside_truth"]]
    alarm = lambda r: any(c["inside_detected"] for c in r["cameras"].values())
    summary["keep_out_alarm"] = {
        "hit_when_inside": _rate(sum(alarm(r) for r in intrusion), len(intrusion)),
        "false_alarm_when_clear": _rate(sum(alarm(r) for r in clear), len(clear)),
        "per_camera_hit_when_inside": {
            s: _rate(sum(r["cameras"][s]["inside_detected"] for r in intrusion if s in r["cameras"]),
                     sum(s in r["cameras"] for r in intrusion)) for s in sensors}}
    return summary


def truth_people(render, frame, sensors, min_pixels=MIN_VISIBLE_PIXELS):
    """Visible ground-truth boxes in the detector's ``person_detections`` form (geometry check)."""
    return {s: [{"bbox_xyxy": box, "confidence": None}
                for box in visible_workers(render["boxes"].get((frame, s), []), min_pixels)[0].values()]
            for s in sensors}


def frame_numbers(render, stride=1, limit=None):
    frames = sorted(render["truth"])[::stride]
    return frames[:limit] if limit else frames


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--detector", choices=("florence2", "rtdetr", "truth"), default="florence2")
    parser.add_argument("--model-id", help="model path or hub id (not used with --detector truth)")
    parser.add_argument("--num-beams", type=int, default=3, help="Florence-2 only")
    parser.add_argument("--batch", action="store_true", help="run all cameras of a frame in one model call")
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-visible-pixels", type=int, default=MIN_VISIBLE_PIXELS)
    args = parser.parse_args()

    from PIL import Image
    from detectors import DETECTORS, WORKER_SCORE_THRESHOLD

    render = load_render(args.render_dir)
    if args.detector != "truth" and not args.model_id:
        parser.error("--model-id is required for model detectors")
    if args.detector == "truth":
        detector = None
    elif args.detector == "florence2":
        detector = DETECTORS["florence2"](args.model_id, num_beams=args.num_beams)
    else:
        detector = DETECTORS["rtdetr"](args.model_id)
    min_score = WORKER_SCORE_THRESHOLD if args.detector == "rtdetr" else None
    sensors = list(render["cameras"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records, call_ms, wall_started = [], [], time.perf_counter()
    with (args.output_dir / "frames.jsonl").open("w", encoding="utf-8") as out:
        for frame in frame_numbers(render, args.stride, args.limit):
            images = [Image.open(render["dir"] / s / f"{frame:06d}.png").convert("RGB") for s in sensors]
            for sensor, image in zip(sensors, images):
                camera = render["cameras"][sensor]
                if image.size != (camera["width"], camera["height"]):
                    raise ValueError(f"{sensor} frame {frame} is {image.size}, calibration says "
                                     f"{camera['width']}x{camera['height']}")
            if detector is None:
                detections = [{"bboxes": [p["bbox_xyxy"] for p in people], "labels": ["person"] * len(people)}
                              for people in truth_people(render, frame, sensors, args.min_visible_pixels).values()]
            elif args.batch:
                detections, ms = detector.detect_batch(images)
                call_ms.append(ms)
            else:
                detections = []
                for image in images:
                    detection, ms = detector.detect(image)
                    detections.append(detection)
                    call_ms.append(ms)
            people = {s: person_detections(d, im.width, im.height, min_score)
                      for s, d, im in zip(sensors, detections, images)}
            record = score_frame(frame, people, render, args.min_visible_pixels)
            record["labels"] = {s: d.get("labels", []) for s, d in zip(sensors, detections)}
            records.append(record)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    wall = time.perf_counter() - wall_started
    summary = summarize(records)
    images_per_call = len(sensors) if args.batch else 1
    summary.update({
        "detector": args.detector, "model_id": args.model_id, "batch": args.batch,
        "num_beams": args.num_beams if args.detector == "florence2" else None,
        "match_iou": MATCH_IOU, "min_visible_pixels": args.min_visible_pixels,
        "render_dir": str(args.render_dir), "render_fps": render["fps"],
        "model_call_ms": _stats(call_ms),
        "per_camera_hz_model_only": round(1000 * images_per_call / statistics.median(call_ms) / len(sensors), 3)
        if call_ms else None,
        "per_camera_hz_wall": round(len(records) / wall, 3) if wall else None})
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
