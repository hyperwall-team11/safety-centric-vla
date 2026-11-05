"""Subscribe to MuJoCo ROS 2 cameras and run a person detector on recent frames.

This records detections for CV integration and does not publish safety Observations.
Florence-2 <OD> has no confidence score; RT-DETR scores are checked against the WORKER
threshold, and every returned label is recorded for miss analysis. With several
``--topic`` options one detector serves the cameras in turn, newest frame per camera, or all
cameras in one model call with ``--batch-cameras``.
"""

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import rclpy
import torch
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image as RosImage

from camera_frames import (LatestFrameBuffer, frame_file_name, person_detections, rgb_image,
                           take_round_robin)
from detectors import DETECTORS, WORKER_SCORE_THRESHOLD
from florence_detect import percentile


DEFAULT_TOPIC = "/cctv_1/image_raw"
DEFAULT_MODELS = {"florence2": "microsoft/Florence-2-base", "rtdetr": "PekingU/rtdetr_r50vd"}


class TopicStats:
    def __init__(self, topic):
        self.topic = topic
        self.frames = LatestFrameBuffer()
        self.processed = 0
        self.stale = 0
        self.invalid = 0
        self.processing_ms = []
        self.queue_age_ms = []

    def summary(self, now):
        counters = self.frames.snapshot()
        first = counters.pop("first_received_monotonic")
        counters.pop("last_received_monotonic")
        elapsed = 0.0 if first is None else now - first
        return {
            "processed": self.processed, **counters, "stale_pending": self.stale,
            "invalid_frames": self.invalid, "local_window_seconds": elapsed,
            "local_processing_hz": self.processed / elapsed if elapsed > 0 else 0.0,
            "p95_processing_ms": percentile(self.processing_ms, 0.95) if self.processing_ms else None,
            "p95_queue_age_ms": percentile(self.queue_age_ms, 0.95) if self.queue_age_ms else None,
        }


class CameraDetector(Node):
    def __init__(self, detector, args):
        super().__init__("cv_camera_detector")
        self.detector = detector
        self.args = args
        self.output = args.output
        self.topics = [TopicStats(topic) for topic in args.topic]
        self.next_index = 0
        self.processed = 0
        self.saved_misses = 0
        self.finished = False
        self.stop_reason = "interrupted"
        self.started = time.monotonic()
        self.processing_ms = []
        self.queue_age_ms = []
        self.file = self.output.open("w", encoding="utf-8")
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.VOLATILE)
        # Separate callback groups and two executor threads let reception continue
        # during slow inference. The default group serializes inference callbacks.
        self.receive_group = MutuallyExclusiveCallbackGroup()
        for stats in self.topics:
            self.create_subscription(RosImage, stats.topic, stats.frames.put, qos,
                                     callback_group=self.receive_group)
        self.create_timer(0.01, self._process_latest)
        self.get_logger().info(
            f"Listening on {', '.join(args.topic)}; writing detections to {self.output}")

    def _check_stop(self, now):
        snapshots = [stats.frames.snapshot() for stats in self.topics]
        for stats, counters in zip(self.topics, snapshots):
            last = counters["last_received_monotonic"]
            if now - (last if last is not None else self.started) >= self.args.idle_timeout_seconds:
                self.stop_reason, self.finished = "input_timeout", True
                self.get_logger().error(
                    f"Camera input timed out on {stats.topic}; check the publisher and ROS graph")
                return True
        firsts = [c["first_received_monotonic"] for c in snapshots
                  if c["first_received_monotonic"] is not None]
        if (self.args.duration_seconds is not None and firsts
                and now - min(firsts) >= self.args.duration_seconds):
            self.stop_reason, self.finished = "duration_reached", True
            return True
        return False

    def _save_miss(self, image, sensor_id, stamp):
        if self.args.save_misses_dir is None or self.saved_misses >= self.args.max_saved_misses:
            return None
        path = self.args.save_misses_dir / frame_file_name(sensor_id, stamp.sec, stamp.nanosec)
        image.save(path)
        self.saved_misses += 1
        return str(path)

    def _take_frames(self):
        """One frame in turn, or with ``--batch-cameras`` the latest frame of every camera that has one."""
        buffers = [stats.frames for stats in self.topics]
        if not self.args.batch_cameras:
            index, frame = take_round_robin(buffers, self.next_index)
            if frame is None:
                return []
            self.next_index = (index + 1) % len(self.topics)
            return [(self.topics[index], frame)]
        return [(stats, frame) for stats, frame in ((s, s.frames.take()) for s in self.topics)
                if frame is not None]

    def _process_latest(self):
        if self.finished:
            return
        now = time.monotonic()
        if self._check_stop(now):
            return
        batch = []
        started = time.perf_counter()
        for stats, frame in self._take_frames():
            age_ms = (time.monotonic() - frame.received_monotonic) * 1000
            if age_ms > self.args.max_frame_age_ms:
                stats.stale += 1
                continue
            try:
                image = rgb_image(frame.message)
            except ValueError as error:
                self.get_logger().warning(f"Skipping camera frame from {stats.topic}: {error}")
                stats.invalid += 1
                continue
            batch.append((stats, frame, age_ms, image))
        if not batch:
            return
        detections, inference_ms = self.detector.detect_batch([item[3] for item in batch])
        people_list = [person_detections(detection, frame.message.width, frame.message.height,
                                         WORKER_SCORE_THRESHOLD)
                       for detection, (_, frame, _, _) in zip(detections, batch)]
        # 묶음 처리에서는 처리 시간이 같은 묶음의 카메라 모두에 공통이다.
        processing_ms = (time.perf_counter() - started) * 1000

        for (stats, frame, age_ms, image), detection, people in zip(batch, detections, people_list):
            msg = frame.message
            stamp = msg.header.stamp
            sensor_id = msg.header.frame_id.removeprefix("camera_")
            record = {
                "topic": stats.topic,
                "sensor_id": sensor_id,
                "frame_id": msg.header.frame_id,
                "stamp": {"sec": stamp.sec, "nanosec": stamp.nanosec},
                "stamp_clock": "publisher_ros_clock",
                "received_at_utc": frame.received_at_utc,
                "processed_at_utc": datetime.now(timezone.utc).isoformat(),
                "queue_age_ms": age_ms,
                "image_size": [msg.width, msg.height],
                "detector": self.detector.name,
                "task": self.detector.task,
                "batch_size": len(batch),
                "people": people,
                "pending_confidence": detection.get("scores") is None and bool(people),
                # 모든 라벨을 남겨 미탐지가 '못 찾음'인지 '다른 라벨'인지 구분한다.
                "all_detections": {"labels": detection["labels"], "bboxes": detection["bboxes"],
                                   "scores": detection.get("scores")},
                "worker_score_threshold": WORKER_SCORE_THRESHOLD if detection.get("scores") is not None else None,
                "generation_ms": inference_ms,
                "processing_ms": processing_ms,
                # 작업자를 못 찾은 실제 입력 영상. 저장 시간은 processing_ms에 넣지 않는다.
                "saved_frame": None if people else self._save_miss(image, sensor_id, stamp),
                "note": "Detection record only; not an approved safety Observation or HazardEvent.",
            }
            self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
            for target in (stats, self):
                target.processing_ms.append(processing_ms)
                target.queue_age_ms.append(age_ms)
                target.processed += 1
            if self.processed == 1 or self.processed % 30 == 0:
                received = sum(s.frames.snapshot()["received_callbacks"] for s in self.topics)
                self.get_logger().info(
                    f"processed={self.processed} received={received} people={len(people)} topic={stats.topic}")
        self.file.flush()
        if self.args.max_frames is not None and self.processed >= self.args.max_frames:
            self.stop_reason, self.finished = "max_frames_reached", True

    def close(self):
        self.file.close()
        now = time.monotonic()
        per_topic = {stats.topic: stats.summary(now) for stats in self.topics}
        firsts = [s.frames.snapshot()["first_received_monotonic"] for s in self.topics]
        firsts = [value for value in firsts if value is not None]
        elapsed = now - min(firsts) if firsts else 0.0
        summary = {
            "topics": [stats.topic for stats in self.topics], "detector": self.detector.name,
            "model": self.args.model, "gpu": torch.cuda.get_device_name(0),
            "stop_reason": self.stop_reason, "processed": self.processed,
            "received_callbacks": sum(t["received_callbacks"] for t in per_topic.values()),
            "replaced_pending": sum(t["replaced_pending"] for t in per_topic.values()),
            "stale_pending": sum(t["stale_pending"] for t in per_topic.values()),
            "invalid_frames": sum(t["invalid_frames"] for t in per_topic.values()),
            "local_window_seconds": elapsed,
            "local_processing_hz": self.processed / elapsed if elapsed > 0 else 0.0,
            "p95_processing_ms": percentile(self.processing_ms, 0.95) if self.processing_ms else None,
            "p95_queue_age_ms": percentile(self.queue_age_ms, 0.95) if self.queue_age_ms else None,
            "batch_cameras": self.args.batch_cameras, "num_beams": self.args.num_beams,
            "per_topic": per_topic,
            "saved_misses": self.saved_misses,
            "measurement_note": (
                "Local window starts at the first received callback on any topic; includes cold inference "
                "and result writes. Per-topic rates share one detector. Queue age uses local monotonic time "
                "since receipt; not capture/network/end-to-end latency. DDS drops before callbacks are not "
                "counted. Publisher ROS stamps are preserved without assuming they share a clock with UTC. "
                "This does not validate a safety KPI."
            ),
        }
        summary_path = self.output.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Summary saved to: {summary_path.resolve()}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", action="append",
                        help=f"camera image topic; repeat for several cameras (default {DEFAULT_TOPIC})")
    parser.add_argument("--detector", choices=sorted(DETECTORS), default="florence2")
    parser.add_argument("--model", help="model id or local path (default depends on --detector)")
    parser.add_argument("--output", type=Path, default=Path("vlm_results/ros_camera.jsonl"))
    stop = parser.add_mutually_exclusive_group()
    stop.add_argument("--max-frames", type=int)
    stop.add_argument("--duration-seconds", type=float)
    parser.add_argument("--idle-timeout-seconds", type=float, default=15)
    parser.add_argument("--max-frame-age-ms", type=float, default=500)
    parser.add_argument("--save-misses-dir", type=Path,
                        help="save input frames with no WORKER detection as PNG for miss analysis")
    parser.add_argument("--max-saved-misses", type=int, default=300)
    parser.add_argument("--batch-cameras", action="store_true",
                        help="run the latest frame of every camera in one model call")
    parser.add_argument("--num-beams", type=int, default=3, help="Florence-2 beam search width")
    args = parser.parse_args()
    args.topic = args.topic or [DEFAULT_TOPIC]
    args.model = args.model or DEFAULT_MODELS[args.detector]
    for topic in args.topic:
        if not topic.startswith("/") or not topic.endswith("/image_raw"):
            parser.error(f"--topic must be an absolute .../image_raw topic: {topic}")
    if len(set(args.topic)) != len(args.topic):
        parser.error("--topic values must be distinct")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be positive")
    if args.num_beams <= 0:
        parser.error("--num-beams must be positive")
    if args.num_beams != 3 and args.detector != "florence2":
        parser.error("--num-beams applies to florence2 only")
    if args.max_saved_misses <= 0:
        parser.error("--max-saved-misses must be positive")
    for name in ("duration_seconds", "idle_timeout_seconds", "max_frame_age_ms"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"--{name.replace('_', '-')} must be positive and finite")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable. Run with ROS 2 and CUDA PyTorch in the same Python environment.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.save_misses_dir is not None:
        args.save_misses_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading {args.detector} {args.model} on {torch.cuda.get_device_name(0)}...", flush=True)
    options = {"num_beams": args.num_beams} if args.detector == "florence2" else {}
    detector = DETECTORS[args.detector](args.model, **options)

    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=2)
    try:
        node = CameraDetector(detector, args)
        executor.add_node(node)
        while rclpy.ok() and not node.finished:
            executor.spin_once(timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception:
        if node is not None:
            node.stop_reason = "error"
        raise
    finally:
        executor.shutdown()
        if node is not None:
            node.close()
            node.destroy_node()
        rclpy.try_shutdown()
    if node is not None and node.stop_reason == "input_timeout":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
