"""Check detector output conversion and WORKER score filtering without models or a GPU."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_frames import person_detections
from detectors import RECORD_SCORE_THRESHOLD, WORKER_SCORE_THRESHOLD, rtdetr_detection


class Values(list):
    """Stand-in for a tensor: only ``tolist()`` is used."""

    def tolist(self):
        return list(self)


class DetectorConversionTests(unittest.TestCase):
    def test_rtdetr_result_is_labelled_and_clipped_to_the_image(self):
        result = {"scores": Values([0.9, 0.4]), "labels": Values([0, 56]),
                  "boxes": Values([[-3.0, 10.0, 50.0, 500.0], [5.0, 5.0, 20.0, 20.0]])}
        detection = rtdetr_detection(result, {0: "person", 56: "chair"}, 640, 480)
        self.assertEqual(detection["labels"], ["person", "chair"])
        self.assertEqual(detection["bboxes"][0], [0.0, 10.0, 50.0, 480.0])
        self.assertEqual(detection["scores"], [0.9, 0.4])

    def test_boxes_outside_the_image_are_dropped(self):
        result = {"scores": Values([0.8]), "labels": Values([0]),
                  "boxes": Values([[700.0, 10.0, 800.0, 50.0]])}
        self.assertEqual(rtdetr_detection(result, {0: "person"}, 640, 480)["bboxes"], [])

    def test_unknown_label_id_is_kept_as_text(self):
        result = {"scores": Values([0.7]), "labels": Values([99]), "boxes": Values([[1, 1, 5, 5]])}
        self.assertEqual(rtdetr_detection(result, {}, 640, 480)["labels"], ["99"])

    def test_worker_threshold_filters_scored_people_only(self):
        self.assertLess(RECORD_SCORE_THRESHOLD, WORKER_SCORE_THRESHOLD)
        scored = {"bboxes": [[0, 0, 10, 10], [20, 20, 30, 30]], "labels": ["person", "person"],
                  "scores": [0.49, 0.5]}
        people = person_detections(scored, 640, 480, WORKER_SCORE_THRESHOLD)
        self.assertEqual([p["confidence"] for p in people], [0.5])
        unscored = {"bboxes": [[0, 0, 10, 10]], "labels": ["person"]}
        self.assertEqual(len(person_detections(unscored, 640, 480, WORKER_SCORE_THRESHOLD)), 1)


if __name__ == "__main__":
    unittest.main()
