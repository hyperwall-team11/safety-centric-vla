"""Exercise real RGB bytes and buffering without ROS, models or a GPU."""

import sys
import unittest
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_frames import (LatestFrameBuffer, frame_file_name, person_detections, rgb_image,
                           take_round_robin)


class CameraFrameTests(unittest.TestCase):
    def test_padded_rgb_rows_are_decoded_without_padding(self):
        msg = SimpleNamespace(encoding="rgb8", width=1, height=2, step=4,
                              data=bytes([255, 0, 0, 99, 0, 255, 0, 88]))
        image = rgb_image(msg)
        self.assertEqual(image.size, (1, 2))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(image.getpixel((0, 1)), (0, 255, 0))

    def test_invalid_image_payloads_are_rejected(self):
        valid = dict(encoding="rgb8", width=1, height=1, step=3, data=b"rgb")
        for override in ({"encoding": "bgr8"}, {"step": 2}, {"data": b"rg"},
                         {"data": b"rgba"}, {"width": 0}, {"height": -1}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                rgb_image(SimpleNamespace(**(valid | override)))

    def test_latest_frame_is_retained_while_consumer_is_busy(self):
        frames = LatestFrameBuffer()
        frames.put("first")
        busy, release = Event(), Event()
        consumed = []

        def consumer():
            consumed.append(frames.take().message)
            busy.set()
            release.wait(5)
            frame = frames.take()
            consumed.append(frame.message if frame else None)

        thread = Thread(target=consumer)
        thread.start()
        try:
            self.assertTrue(busy.wait(5))
            for index in range(100):
                frames.put(index)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(consumed, ["first", 99])
        self.assertIsNone(frames.take())
        counters = frames.snapshot()
        self.assertEqual(counters["received_callbacks"], 101)
        self.assertEqual(counters["replaced_pending"], 99)
        self.assertLessEqual(counters["first_received_monotonic"],
                             counters["last_received_monotonic"])

    def test_received_time_has_a_utc_offset(self):
        frames = LatestFrameBuffer()
        frames.put("frame")
        self.assertTrue(frames.take().received_at_utc.endswith("+00:00"))

    def test_round_robin_gives_each_camera_a_turn(self):
        first, second = LatestFrameBuffer(), LatestFrameBuffer()
        buffers = [first, second]
        self.assertEqual(take_round_robin(buffers, 0), (None, None))
        first.put("a1")
        second.put("b1")
        index, frame = take_round_robin(buffers, 0)
        self.assertEqual((index, frame.message), (0, "a1"))
        first.put("a2")
        index, frame = take_round_robin(buffers, index + 1)
        self.assertEqual((index, frame.message), (1, "b1"))
        index, frame = take_round_robin(buffers, (index + 1) % 2)
        self.assertEqual((index, frame.message), (0, "a2"))
        second.put("b2")
        index, frame = take_round_robin(buffers, 0)
        self.assertEqual((index, frame.message), (1, "b2"))

    def test_frame_file_name_is_safe_and_stamp_ordered(self):
        self.assertEqual(frame_file_name("cctv_1", 12, 5), "cctv_1_12_000000005.png")
        self.assertEqual(frame_file_name("../x y", 1, 0), "___x_y_1_000000000.png")
        self.assertEqual(frame_file_name("", 1, 0), "camera_1_000000000.png")

    def test_unscored_person_preserves_missing_confidence(self):
        result = person_detections({"bboxes": [[0, 0, 10, 10], [1, 1, 2, 2]],
                                    "labels": ["person", "box"]}, 640, 480)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["confidence"])

    def test_scored_person_is_validated(self):
        result = person_detections({"bboxes": [[0, 0, 10, 10]], "labels": ["PERSON"],
                                    "scores": [0.8]}, 640, 480)
        self.assertEqual(result[0]["confidence"], 0.8)

    def test_invalid_detection_arrays_and_scores_are_rejected(self):
        cases = [dict(bboxes=[[0, 0, 1, 1]], labels=[]),
                 dict(bboxes=[[0, 0, 1, 1]], labels=["person"], scores=[])]
        cases.extend(dict(bboxes=[[0, 0, 1, 1]], labels=["person"], scores=[value])
                     for value in (None, True, "0.5", -1, 2, float("nan"), float("inf")))
        for detection in cases:
            with self.subTest(detection=detection), self.assertRaises(ValueError):
                person_detections(detection, 640, 480)

    def test_invalid_person_boxes_are_rejected(self):
        for box in ([1, 1, 0, 0], [0, 0, 641, 480], [0, 0, 1],
                    [0, 0, float("nan"), 1], [0, 0, 1, float("inf")]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                person_detections(dict(bboxes=[box], labels=["person"]), 640, 480)


if __name__ == "__main__":
    unittest.main()
