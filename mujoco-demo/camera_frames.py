"""ROS-independent frame buffering and validation for the camera subscriber."""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from PIL import Image


@dataclass(frozen=True)
class ReceivedFrame:
    message: object
    received_monotonic: float
    received_at_utc: str


class LatestFrameBuffer:
    """Keep one pending frame while the inference callback is occupied."""

    def __init__(self):
        self.lock = Lock()
        self.latest = None
        self.received = 0
        self.replaced = 0
        self.first_received = None
        self.last_received = None

    def put(self, message):
        now = time.monotonic()
        with self.lock:
            self.received += 1
            self.replaced += self.latest is not None
            self.latest = ReceivedFrame(message, now, datetime.now(timezone.utc).isoformat())
            if self.first_received is None:
                self.first_received = now
            self.last_received = now

    def take(self):
        with self.lock:
            frame, self.latest = self.latest, None
            return frame

    def snapshot(self):
        with self.lock:
            return {"received_callbacks": self.received, "replaced_pending": self.replaced,
                    "first_received_monotonic": self.first_received,
                    "last_received_monotonic": self.last_received}


def rgb_image(msg):
    """Convert rgb8 data, including padded rows, without importing ROS 2."""
    if msg.encoding != "rgb8":
        raise ValueError(f"Expected rgb8 camera image, received {msg.encoding!r}")
    if msg.width <= 0 or msg.height <= 0 or msg.step < msg.width * 3:
        raise ValueError("Invalid camera dimensions or row stride")
    data = bytes(msg.data)
    if len(data) != msg.step * msg.height:
        raise ValueError("Camera image byte count does not match dimensions")
    return Image.frombytes("RGB", (msg.width, msg.height), data, "raw", "RGB", msg.step, 1)


def person_detections(detection, width, height):
    boxes, labels = detection.get("bboxes", []), detection.get("labels", [])
    scores = detection.get("scores")
    if len(boxes) != len(labels) or (scores is not None and len(scores) != len(boxes)):
        raise ValueError("Detector returned mismatched arrays")
    people = []
    for index, (box, label) in enumerate(zip(boxes, labels)):
        if str(label).casefold() != "person":
            continue
        if len(box) != 4:
            raise ValueError("Expected an xyxy box with four coordinates")
        x1, y1, x2, y2 = (float(value) for value in box)
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError("Detector returned an invalid pixel box")
        score = None if scores is None else scores[index]
        if scores is not None and (isinstance(score, bool) or not isinstance(score, (int, float))
                                   or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError("Detector confidence must be finite and within [0, 1]")
        people.append({"bbox_xyxy": [x1, y1, x2, y2],
                       "confidence": None if score is None else float(score)})
    return people
