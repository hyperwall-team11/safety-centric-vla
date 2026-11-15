"""Check Sensor heartbeats, Worker snapshots and the background sender without ROS or a backend."""

import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hazard_reporter import camera_from_pose
from perception_feed import (LatestSender, WorkerFusion, rotation_to_quaternion, sensor_payload,
                             zone_rings)

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
KEEP_OUT = {"zone_id": "keep-out", "zone_type": "KEEP_OUT", "frame_id": "world",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}
WORK = {"zone_id": "A", "zone_type": "WORK", "frame_id": "world",
        "geometry": {"type": "Polygon", "coordinates": [[[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]]]}}


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def point(x, y):
    return {"x": x, "y": y, "z": 0.0, "in_zone": False}


class PerceptionFeedTests(unittest.TestCase):
    def test_quaternion_from_rotation(self):
        self.assertEqual(rotation_to_quaternion([[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
                         {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0})
        q = rotation_to_quaternion([[0, -1, 0], [1, 0, 0], [0, 0, 1]])  # z축 90도
        self.assertAlmostEqual(q["z"], 2 ** -0.5)
        self.assertAlmostEqual(q["w"], 2 ** -0.5)
        q = rotation_to_quaternion([[1, 0, 0], [0, -1, 0], [0, 0, -1]])  # x축 180도
        self.assertAlmostEqual(abs(q["x"]), 1.0)

    def test_sensor_status_follows_frame_age(self):
        camera = camera_from_pose([1, 2, 3], [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 60)
        fresh = sensor_payload("cctv_1", "/cctv_1/image_raw", camera, ["A"], at(0), at(1.9))
        self.assertEqual(fresh["status"], "ONLINE")
        self.assertEqual(fresh["frame_id"], "camera_cctv_1")
        self.assertEqual(fresh["source_uri"], "ros2:///cctv_1/image_raw")
        self.assertEqual(fresh["covered_zone_ids"], ["A"])
        self.assertEqual(fresh["pose"]["position"], {"x": 1, "y": 2, "z": 3})
        self.assertIsNone(fresh["mounted_robot_id"])
        self.assertEqual(sensor_payload("cctv_1", "/t", camera, [], at(0), at(2.0))["status"], "DEGRADED")
        self.assertEqual(sensor_payload("cctv_1", "/t", camera, [], None, at(0))["status"], "DEGRADED")
        self.assertEqual(sensor_payload("cctv_1", "/t", camera, [], at(0), at(0), offline=True)["status"],
                         "OFFLINE")

    def test_non_polygon_zones_are_skipped(self):
        point_zone = {"zone_id": "p", "frame_id": "world", "geometry": {"type": "Point"}}
        map_zone = dict(WORK, zone_id="m", frame_id="map")
        self.assertEqual([zone_id for zone_id, _ in zone_rings([KEEP_OUT, point_zone, map_zone])],
                         ["keep-out"])

    def test_contract_polygon_points_zone_is_used(self):
        # 백엔드 계약 형식 polygon/points(열린 고리)도 GeoJSON과 같은 구역으로 읽는다.
        contract = {"zone_id": "keep-out", "zone_type": "KEEP_OUT", "frame_id": "world",
                    "geometry": {"type": "polygon", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}}
        self.assertEqual(zone_rings([contract]), zone_rings([KEEP_OUT]))
        fusion = WorkerFusion([contract], ["keep-out"])
        fusion.update("cctv_1", at(0), [point(0.5, 0.5)])
        worker = fusion.snapshot(at(0.1))["workers"][0]
        self.assertEqual((worker["current_zone_id"], worker["safety_status"]), ("keep-out", "DANGER"))

    def test_keep_out_wins_over_overlapping_zone_in_any_order(self):
        square = [[-1, -1], [2, -1], [2, 2], [-1, 2], [-1, -1]]
        overlap = dict(WORK, geometry={"type": "Polygon", "coordinates": [square]})
        for zones in ([overlap, KEEP_OUT], [KEEP_OUT, overlap]):
            fusion = WorkerFusion(zones, ["keep-out"])
            fusion.update("cctv_1", at(0), [point(0.5, 0.5), point(1.5, 1.5)])
            workers = fusion.snapshot(at(0.1))["workers"]
            self.assertEqual([(w["current_zone_id"], w["safety_status"]) for w in workers],
                             [("keep-out", "DANGER"), ("A", "UNKNOWN")])

    def test_cameras_seeing_the_same_worker_are_merged(self):
        fusion = WorkerFusion([KEEP_OUT, WORK], ["keep-out"])
        fusion.update("cctv_1", at(0.0), [point(0.4, 0.5)])
        fusion.update("cctv_2", at(0.1), [point(0.6, 0.5)])
        snapshot = fusion.snapshot(at(0.2))
        self.assertEqual(snapshot["observed_at"], at(0.0).isoformat())
        [worker] = snapshot["workers"]
        self.assertAlmostEqual(worker["pose"]["position"]["x"], 0.5)
        self.assertEqual(worker["pose"]["frame_id"], "world")
        self.assertEqual(worker["current_zone_id"], "keep-out")
        self.assertEqual(worker["safety_status"], "DANGER")
        self.assertEqual(worker["source_sensor_ids"], ["cctv_1", "cctv_2"])
        self.assertEqual(worker["last_seen_at"], at(0.1).isoformat())
        self.assertEqual(worker["confidence"], 1.0)

    def test_two_people_in_one_camera_stay_separate(self):
        fusion = WorkerFusion([KEEP_OUT, WORK], ["keep-out"])
        fusion.update("cctv_1", at(0), [point(2.5, 0.5), point(2.6, 0.5)])
        workers = fusion.snapshot(at(0.1))["workers"]
        self.assertEqual(len(workers), 2)
        self.assertEqual({w["current_zone_id"] for w in workers}, {"A"})
        self.assertEqual({w["safety_status"] for w in workers}, {"UNKNOWN"})

    def test_worker_outside_every_zone_has_empty_zone(self):
        fusion = WorkerFusion([KEEP_OUT])
        fusion.update("cctv_1", at(0), [point(5, 5)])
        self.assertEqual(fusion.snapshot(at(0.1))["workers"][0]["current_zone_id"], "")

    def test_stale_camera_is_left_out_and_empty_view_is_reported(self):
        fusion = WorkerFusion([KEEP_OUT])
        fusion.update("cctv_1", at(0), [point(0.5, 0.5)])
        fusion.update("cctv_2", at(1.5), [])
        snapshot = fusion.snapshot(at(1.6))  # cctv_1은 1.6초 지나 제외
        self.assertEqual(snapshot, {"observed_at": at(1.5).isoformat(), "workers": []})
        self.assertIsNone(fusion.snapshot(at(3.0)))  # 모든 카메라가 오래됨

    def test_snapshot_is_sent_only_when_newer(self):
        fusion = WorkerFusion([KEEP_OUT])
        fusion.update("cctv_1", at(0), [])
        self.assertIsNotNone(fusion.snapshot(at(0.1)))
        self.assertIsNone(fusion.snapshot(at(0.2)))
        fusion.update("cctv_1", at(0.3), [])
        self.assertEqual(fusion.snapshot(at(0.4))["observed_at"], at(0.3).isoformat())

    def test_confidence_is_recent_seen_ratio(self):
        fusion = WorkerFusion([KEEP_OUT])
        for second, seen in enumerate([False, False, True, False, True]):
            fusion.update("cctv_1", at(second * 0.1), [point(0.5, 0.5)] if seen else [])
        self.assertEqual(fusion.snapshot(at(0.5))["workers"][0]["confidence"], 0.4)

    def test_sender_keeps_latest_value_and_counts_failures(self):
        release, sent, errors = threading.Event(), [], []

        def send(value):
            release.wait(5)
            if value == "bad":
                raise RuntimeError("down")
            sent.append(value)

        sender = LatestSender(send, lambda key, exc: errors.append(key))
        sender.submit("a", "first")
        sender.submit("b", "bad")
        sender.submit("b", "replaced-before-send")
        release.set()
        sender.close()
        self.assertEqual(sent, ["first", "replaced-before-send"])

        failing = LatestSender(send, lambda key, exc: errors.append(key), retry_delay=0, max_retries=2)
        failing.submit("c", "bad")
        failing.close()
        self.assertEqual((failing.failures, errors), (3, ["c", "c", "c"]))

    def test_sender_retries_a_failed_payload_until_it_goes_through(self):
        attempts, sent = [], []

        def send(value):
            attempts.append(value)
            if len(attempts) < 3:
                raise RuntimeError("backend restarting")
            sent.append(value)

        sender = LatestSender(send, retry_delay=0, max_retries=2)
        sender.submit("workers", "snapshot-1")
        sender.close()
        self.assertEqual((attempts, sent, sender.failures), (["snapshot-1"] * 3, ["snapshot-1"], 2))

    def test_sender_sends_newer_value_instead_of_retrying_old_one(self):
        first_failed, sent = threading.Event(), []

        def send(value):
            if value == "old":
                first_failed.set()
                raise RuntimeError("down")
            sent.append(value)

        sender = LatestSender(send, retry_delay=1.0, max_retries=2)
        sender.submit("workers", "old")
        first_failed.wait(5)
        sender.submit("workers", "new")
        sender.close()
        self.assertEqual(sent, ["new"])


if __name__ == "__main__":
    unittest.main()
