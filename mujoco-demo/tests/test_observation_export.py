"""CPU checks of projection geometry and the confidence export boundary."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observation_export import export, ground_position


class ObservationExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # Camera looks straight down from 2 m, with 90 degree vertical FOV.
        self.camera = {
            "position_m": [1, 2, 2],
            "rotation_cam_to_world": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "fovy_deg": 90,
            "ground_plane_z_m": 0,
        }
        self.capture = {
            "image": "frame.png", "image_sha256": "a" * 64,
            "sensor_id": "cctv_1", "timestamp": "2026-10-30T00:00:00Z",
            "frame_id": "world", "width": 640, "height": 480,
            "camera": self.camera,
            "ground_truth_worker_position_m": [999, 999, 999],
        }
        self.result = {
            "image": "frame.png", "image_sha256": "a" * 64, "task": "<OD>",
            "detections": {"<OD>": {"bboxes": [[300, 100, 340, 240]],
                                     "labels": ["person"]}},
        }

    def run_export(self):
        metadata = self.root / "metadata.json"
        result_path = self.root / "frame.json"
        metadata.write_text(json.dumps([self.capture]), encoding="utf-8")
        result_path.write_text(json.dumps(self.result), encoding="utf-8")
        return export([result_path], metadata)

    def test_bottom_centre_ray_intersects_known_floor_point(self):
        self.assertEqual(ground_position([300, 100, 340, 240], 640, 480,
                                         self.camera), {"x": 1, "y": 2, "z": 0})
        point = ground_position([540, 100, 580, 240], 640, 480, self.camera)
        self.assertAlmostEqual(point["x"], 3)
        self.assertAlmostEqual(point["y"], 2)

    def test_rotation_and_nonzero_floor_are_used(self):
        camera = copy.deepcopy(self.camera)
        camera["rotation_cam_to_world"] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        camera["ground_plane_z_m"] = 1
        point = ground_position([540, 100, 580, 240], 640, 480, camera)
        self.assertAlmostEqual(point["x"], 1)
        self.assertAlmostEqual(point["y"], 3)
        self.assertEqual(point["z"], 1)

    def test_invalid_boxes_are_rejected(self):
        for box in ([340, 100, 300, 240], [-1, 100, 340, 240],
                    [300, 100, 641, 240], [300, 100, 340, float("nan")]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                ground_position(box, 640, 480, self.camera)

    def test_upward_camera_is_rejected(self):
        self.camera["rotation_cam_to_world"] = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
        with self.assertRaises(ValueError):
            ground_position([300, 100, 340, 240], 640, 480, self.camera)

    def test_unscored_person_is_held_and_ground_truth_is_not_used(self):
        data = self.run_export()
        self.assertEqual(data["observations"], [])
        candidate = data["pending_confidence"][0]["candidate"]
        self.assertNotIn("confidence", candidate)
        self.assertEqual(candidate["position"], {"x": 1, "y": 2, "z": 0})

    def test_scored_person_is_exported_with_stable_id(self):
        self.result["detections"]["<OD>"]["scores"] = [0.7]
        first = self.run_export()
        second = self.run_export()
        self.assertEqual(first["pending_confidence"], [])
        record = first["observations"][0]
        self.assertEqual(record["confidence"], 0.7)
        self.assertEqual(record["detected_type"], "WORKER")
        self.assertEqual(record["observation_id"], second["observations"][0]["observation_id"])

    def test_hash_mismatch_and_missing_hash_are_rejected(self):
        for value in (None, "b" * 64):
            self.result["image_sha256"] = value
            with self.subTest(hash=value), self.assertRaises(ValueError):
                self.run_export()

    def test_timestamp_requires_a_timezone(self):
        self.capture["timestamp"] = "2026-10-30T00:00:00"
        with self.assertRaises(ValueError):
            self.run_export()

    def test_invalid_confidence_is_rejected(self):
        for score in (True, "0.7", -0.1, 1.1, float("nan"), float("inf")):
            self.result["detections"]["<OD>"]["scores"] = [score]
            with self.subTest(score=score), self.assertRaises(ValueError):
                self.run_export()

    def test_unmapped_label_is_skipped(self):
        self.result["detections"]["<OD>"]["labels"] = ["box"]
        data = self.run_export()
        self.assertEqual(data["observations"], [])
        self.assertEqual(data["pending_confidence"], [])
        self.assertEqual(len(data["skipped"]), 1)

    def test_mismatched_detection_arrays_are_rejected(self):
        self.result["detections"]["<OD>"]["labels"] = []
        with self.assertRaises(ValueError):
            self.run_export()


if __name__ == "__main__":
    unittest.main()
