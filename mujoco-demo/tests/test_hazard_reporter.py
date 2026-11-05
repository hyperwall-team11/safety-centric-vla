"""Check KEEP_OUT intrusion logic and the HazardEvent payload without ROS, MuJoCo or a backend."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hazard_reporter import (IntrusionTracker, build_hazard, camera_from_pose, keep_out_ring,
                             observation_id, point_in_ring, workers_in_zone)

SQUARE = [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]


def zone(**override):
    return {"zone_id": "keep-out", "zone_type": "KEEP_OUT", "frame_id": "world",
            "geometry": {"type": "Polygon", "coordinates": [SQUARE]}} | override


class HazardReporterTests(unittest.TestCase):
    def test_point_in_ring_counts_edges_as_inside(self):
        ring = keep_out_ring(zone())
        self.assertTrue(point_in_ring(0, 0, ring))
        self.assertTrue(point_in_ring(1, 0, ring))
        self.assertFalse(point_in_ring(1.01, 0, ring))

    def test_invalid_keep_out_zones_are_rejected(self):
        for override in ({"zone_type": "WORK"}, {"frame_id": "map"},
                         {"geometry": {"type": "Point", "coordinates": [0, 0]}},
                         {"geometry": {"type": "Polygon", "coordinates": [SQUARE[:-1]]}},
                         {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0], [0, 0]]]}}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                keep_out_ring(zone(**override))

    def test_worker_below_a_downward_camera_is_inside(self):
        # MuJoCo camera looks along its -z axis, so the identity rotation points at the floor.
        camera = camera_from_pose([0, 0, 3], [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 60)
        centre = {"bbox_xyxy": [300, 200, 340, 240], "confidence": None}
        corner = {"bbox_xyxy": [0, 0, 20, 20], "confidence": None}
        positions, inside = workers_in_zone([centre, corner], 640, 480, camera, keep_out_ring(zone()))
        self.assertTrue(inside)
        self.assertEqual([p["in_zone"] for p in positions], [True, False])
        self.assertAlmostEqual(positions[0]["x"], 0.0)
        self.assertAlmostEqual(positions[0]["y"], 0.0)

    def test_one_camera_needs_two_consecutive_frames(self):
        tracker = IntrusionTracker()
        self.assertIsNone(tracker.update("cctv_1", True, 0.0, ["o1"]))
        self.assertIsNone(tracker.update("cctv_1", False, 0.1))
        self.assertIsNone(tracker.update("cctv_1", True, 0.2, ["o2"]))
        trigger = tracker.update("cctv_1", True, 0.3, ["o3"])
        self.assertEqual(trigger["sensor_id"], "cctv_1")
        self.assertEqual(trigger["observation_ids"], ["o2", "o3"])
        self.assertAlmostEqual(trigger["confidence"], 3 / 4)

    def test_other_camera_missing_the_worker_does_not_block_trigger(self):
        tracker = IntrusionTracker()
        tracker.update("cctv_2", False, 0.0)
        tracker.update("cctv_1", True, 0.1, ["a"])
        tracker.update("cctv_2", False, 0.2)
        self.assertIsNotNone(tracker.update("cctv_1", True, 0.3, ["b"]))

    def test_one_event_per_entry_until_zone_is_clear(self):
        tracker = IntrusionTracker(["cctv_1"])
        tracker.update("cctv_1", True, 0.0)
        first = tracker.update("cctv_1", True, 0.1)
        tracker.mark_reported()
        self.assertIsNone(tracker.update("cctv_1", True, 0.2))
        self.assertIsNone(tracker.update("cctv_1", False, 1.0))  # 2초가 지나지 않음
        self.assertIsNone(tracker.update("cctv_1", True, 1.5))
        self.assertIsNone(tracker.update("cctv_1", False, 3.6))  # 1.5초 이후 2초 넘게 비어 진입 종료
        tracker.update("cctv_1", True, 4.0)
        second = tracker.update("cctv_1", True, 4.1)
        self.assertNotEqual(first["hazard_id"], second["hazard_id"])

    def test_failed_report_is_retried_with_the_same_hazard_id(self):
        tracker = IntrusionTracker(["cctv_1"])
        tracker.update("cctv_1", True, 0.0)
        first = tracker.update("cctv_1", True, 0.1)
        retry = tracker.update("cctv_1", True, 0.2)
        self.assertTrue(first["hazard_id"].startswith("hazard-"))
        self.assertEqual(first["hazard_id"], retry["hazard_id"])

    def test_stalled_camera_does_not_end_the_entry(self):
        tracker = IntrusionTracker(["cctv_1", "cctv_2"])
        tracker.update("cctv_1", True, 0.0)
        first = tracker.update("cctv_1", True, 0.1)
        tracker.mark_reported()
        # cctv_2는 한 번도 결과를 내지 않음: cctv_1이 계속 비어 있어도 관측 불가로 보고 진입을 유지
        self.assertIsNone(tracker.update("cctv_1", False, 5.0))
        tracker.update("cctv_1", True, 5.1)
        self.assertIsNone(tracker.update("cctv_1", True, 5.2))
        # cctv_2가 마지막 목격 뒤 프레임을 내고 2초가 지나야 진입이 끝남
        tracker.update("cctv_2", False, 5.3)
        self.assertIsNone(tracker.update("cctv_1", False, 7.0))
        tracker.update("cctv_1", False, 7.2)
        tracker.update("cctv_1", True, 7.3)
        second = tracker.update("cctv_1", True, 7.4)
        self.assertNotEqual(first["hazard_id"], second["hazard_id"])

    def test_hazard_payload_matches_team_decision(self):
        trigger = {"hazard_id": "hazard-fixed", "sensor_id": "cctv_1", "confidence": 0.6666,
                   "observation_ids": ["o1"]}
        at = datetime(2026, 11, 5, tzinfo=timezone.utc)
        hazard = build_hazard("keep-out", ["robot-01"], trigger, at)
        self.assertEqual(hazard["hazard_id"], "hazard-fixed")
        self.assertEqual(hazard["hazard_type"], "WORKER_INTRUSION")
        self.assertEqual(hazard["severity"], "CRITICAL")
        self.assertEqual(hazard["related_robot_ids"], ["robot-01"])
        self.assertEqual(hazard["zone_id"], "keep-out")
        self.assertEqual(hazard["confidence"], 0.667)
        self.assertEqual(hazard["detected_at"], "2026-11-05T00:00:00+00:00")
        self.assertNotIn("status", hazard)

    def test_observation_id_is_stable(self):
        self.assertEqual(observation_id("cctv_1", 1, 2, 0), observation_id("cctv_1", 1, 2, 0))
        self.assertNotEqual(observation_id("cctv_1", 1, 2, 0), observation_id("cctv_2", 1, 2, 0))


if __name__ == "__main__":
    unittest.main()
