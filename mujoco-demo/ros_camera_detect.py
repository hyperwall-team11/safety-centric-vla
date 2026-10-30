"""Subscribe to a MuJoCo ROS 2 camera and run Florence-2 on recent frames.

This records detections for CV integration. Florence-2 <OD> does not provide a
calibrated confidence score, so this node does not publish safety Observations.
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
from transformers import AutoModelForCausalLM, AutoProcessor

from camera_frames import LatestFrameBuffer, person_detections, rgb_image
from florence_detect import detect_once, percentile


TASK = "<OD>"
DEFAULT_TOPIC = "/cctv_1/image_raw"


class CameraDetector(Node):
    def __init__(self, model, processor, args):
        super().__init__("cv_camera_detector")
        self.model = model
        self.processor = processor
        self.args = args
        self.topic = args.topic
        self.output = args.output
        self.frames = LatestFrameBuffer()
        self.processed = 0
        self.stale = 0
        self.invalid = 0
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
        self.create_subscription(RosImage, self.topic, self.frames.put, qos,
                                 callback_group=self.receive_group)
        self.create_timer(0.01, self._process_latest)
        self.get_logger().info(f"Listening on {self.topic}; writing detections to {self.output}")

    def _process_latest(self):
        if self.finished:
            return
        now = time.monotonic()
        counters = self.frames.snapshot()
        last_received = counters["last_received_monotonic"]
        if now - (last_received if last_received is not None else self.started) >= self.args.idle_timeout_seconds:
            self.stop_reason, self.finished = "input_timeout", True
            self.get_logger().error("Camera input timed out; check the publisher and ROS graph")
            return
        first_received = counters["first_received_monotonic"]
        if (self.args.duration_seconds is not None and first_received is not None
                and now - first_received >= self.args.duration_seconds):
            self.stop_reason, self.finished = "duration_reached", True
            return
        frame = self.frames.take()
        if frame is None:
            return
        age_ms = (time.monotonic() - frame.received_monotonic) * 1000
        if age_ms > self.args.max_frame_age_ms:
            self.stale += 1
            return
        msg = frame.message
        started = time.perf_counter()
        try:
            image = rgb_image(msg)
        except ValueError as error:
            self.get_logger().warning(f"Skipping camera frame: {error}")
            self.invalid += 1
            return
        _, parsed, generation_ms = detect_once(self.model, self.processor, image, TASK)
        detection = parsed.get(TASK, {})
        people = person_detections(detection, msg.width, msg.height)

        stamp = msg.header.stamp
        record = {
            "topic": self.topic,
            "sensor_id": msg.header.frame_id.removeprefix("camera_"),
            "frame_id": msg.header.frame_id,
            "stamp": {"sec": stamp.sec, "nanosec": stamp.nanosec},
            "stamp_clock": "publisher_ros_clock",
            "received_at_utc": frame.received_at_utc,
            "processed_at_utc": datetime.now(timezone.utc).isoformat(),
            "queue_age_ms": age_ms,
            "image_size": [msg.width, msg.height],
            "task": TASK,
            "people": people,
            "pending_confidence": detection.get("scores") is None and bool(people),
            "generation_ms": generation_ms,
            "processing_ms": (time.perf_counter() - started) * 1000,
            "note": "Detection record only; not an approved safety Observation or HazardEvent.",
        }
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()
        self.processing_ms.append(record["processing_ms"])
        self.queue_age_ms.append(age_ms)
        self.processed += 1
        if self.processed == 1 or self.processed % 30 == 0:
            self.get_logger().info(
                f"processed={self.processed} received={self.frames.snapshot()['received_callbacks']} "
                f"people={len(people)}")
        if self.args.max_frames is not None and self.processed >= self.args.max_frames:
            self.stop_reason, self.finished = "max_frames_reached", True

    def close(self):
        self.file.close()
        counters = self.frames.snapshot()
        first = counters.pop("first_received_monotonic")
        counters.pop("last_received_monotonic")
        elapsed = 0.0 if first is None else time.monotonic() - first
        summary = {
            "topic": self.topic, "model": self.args.model, "gpu": torch.cuda.get_device_name(0),
            "stop_reason": self.stop_reason, "processed": self.processed,
            **counters, "stale_pending": self.stale, "invalid_frames": self.invalid,
            "local_window_seconds": elapsed,
            "local_processing_hz": self.processed / elapsed if elapsed > 0 else 0.0,
            "p95_processing_ms": percentile(self.processing_ms, 0.95) if self.processing_ms else None,
            "p95_queue_age_ms": percentile(self.queue_age_ms, 0.95) if self.queue_age_ms else None,
            "measurement_note": (
                "Local window starts at first received callback; includes cold inference and result writes. "
                "Queue age uses local monotonic time since receipt; not capture/network/end-to-end latency. "
                "DDS drops before callbacks are not counted. Publisher ROS stamps are preserved without "
                "assuming they share a clock with UTC. This does not validate a safety KPI."
            ),
        }
        summary_path = self.output.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Summary saved to: {summary_path.resolve()}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--model", default="microsoft/Florence-2-base")
    parser.add_argument("--output", type=Path, default=Path("vlm_results/ros_camera.jsonl"))
    stop = parser.add_mutually_exclusive_group()
    stop.add_argument("--max-frames", type=int)
    stop.add_argument("--duration-seconds", type=float)
    parser.add_argument("--idle-timeout-seconds", type=float, default=15)
    parser.add_argument("--max-frame-age-ms", type=float, default=500)
    args = parser.parse_args()
    if not args.topic.startswith("/") or not args.topic.endswith("/image_raw"):
        parser.error("--topic must be an absolute .../image_raw topic")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be positive")
    for name in ("duration_seconds", "idle_timeout_seconds", "max_frame_age_ms"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"--{name.replace('_', '-')} must be positive and finite")
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable. Run with ROS 2 and CUDA PyTorch in the same Python environment.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Loading {args.model} on {torch.cuda.get_device_name(0)}...", flush=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, trust_remote_code=True, torch_dtype=torch.float16,
        attn_implementation="eager",
    ).to("cuda").eval()
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)

    rclpy.init()
    node = None
    executor = MultiThreadedExecutor(num_threads=2)
    try:
        node = CameraDetector(model, processor, args)
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
