"""Subscribe to a MuJoCo ROS 2 camera and run Florence-2 on recent frames.

This records detections for CV integration. Florence-2 <OD> does not provide a
calibrated confidence score, so this node does not publish safety Observations.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import rclpy
import torch
from PIL import Image
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image as RosImage
from transformers import AutoModelForCausalLM, AutoProcessor

from florence_detect import detect_once


TASK = "<OD>"
DEFAULT_TOPIC = "/cctv_1/image_raw"


def rgb_image(msg):
    """Convert an rgb8 sensor_msgs/Image, including padded rows, to PIL RGB."""
    if msg.encoding != "rgb8":
        raise ValueError(f"Expected rgb8 camera image, received {msg.encoding!r}")
    if msg.width <= 0 or msg.height <= 0 or msg.step < msg.width * 3:
        raise ValueError("Invalid camera dimensions or row stride")
    data = bytes(msg.data)
    if len(data) != msg.step * msg.height:
        raise ValueError("Camera image byte count does not match dimensions")
    return Image.frombytes("RGB", (msg.width, msg.height), data,
                           "raw", "RGB", msg.step, 1)


class CameraDetector(Node):
    def __init__(self, model, processor, topic, output, max_frames):
        super().__init__("cv_camera_detector")
        self.model = model
        self.processor = processor
        self.topic = topic
        self.output = output
        self.max_frames = max_frames
        self.latest = None
        self.received = 0
        self.processed = 0
        self.skipped = 0
        self.file = output.open("w", encoding="utf-8")
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.VOLATILE)
        self.create_subscription(RosImage, topic, self._receive, qos)
        # Inference runs in the spin thread. Keeping only the newest frame avoids
        # building a backlog when model latency exceeds the camera period.
        self.create_timer(0.01, self._process_latest)
        self.get_logger().info(f"Listening on {topic}; writing detections to {output}")

    def _receive(self, msg):
        self.received += 1
        if self.latest is not None:
            self.skipped += 1
        self.latest = msg

    def _process_latest(self):
        msg, self.latest = self.latest, None
        if msg is None:
            return
        try:
            image = rgb_image(msg)
        except ValueError as error:
            self.get_logger().warning(f"Skipping camera frame: {error}")
            return

        started = time.perf_counter()
        _, parsed, generation_ms = detect_once(self.model, self.processor, image, TASK)
        detection = parsed.get(TASK, {})
        boxes = detection.get("bboxes", [])
        labels = detection.get("labels", [])
        scores = detection.get("scores")
        if len(boxes) != len(labels) or (scores is not None and len(scores) != len(boxes)):
            raise ValueError("Florence-2 returned mismatched detection arrays")
        people = []
        for index, (box, label) in enumerate(zip(boxes, labels)):
            if str(label).casefold() == "person":
                people.append({"bbox_xyxy": [float(v) for v in box],
                               "confidence": None if scores is None else float(scores[index])})

        stamp = msg.header.stamp
        record = {
            "topic": self.topic,
            "sensor_id": msg.header.frame_id.removeprefix("camera_"),
            "frame_id": msg.header.frame_id,
            "stamp": {"sec": stamp.sec, "nanosec": stamp.nanosec},
            "processed_at_utc": datetime.now(timezone.utc).isoformat(),
            "image_size": [msg.width, msg.height],
            "task": TASK,
            "people": people,
            "pending_confidence": scores is None and bool(people),
            "generation_ms": generation_ms,
            "processing_ms": (time.perf_counter() - started) * 1000,
            "note": "Detection record only; not an approved safety Observation or HazardEvent.",
        }
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()
        self.processed += 1
        if self.processed == 1 or self.processed % 30 == 0:
            self.get_logger().info(
                f"processed={self.processed} received={self.received} "
                f"replaced_pending={self.skipped} people={len(people)}")
        if self.max_frames is not None and self.processed >= self.max_frames:
            rclpy.shutdown()

    def close(self):
        self.file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--model", default="microsoft/Florence-2-base")
    parser.add_argument("--output", type=Path, default=Path("vlm_results/ros_camera.jsonl"))
    parser.add_argument("--max-frames", type=int)
    args = parser.parse_args()
    if not args.topic.startswith("/") or not args.topic.endswith("/image_raw"):
        parser.error("--topic must be an absolute .../image_raw topic")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be positive")
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
    try:
        node = CameraDetector(model, processor, args.topic, args.output, args.max_frames)
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.close()
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
