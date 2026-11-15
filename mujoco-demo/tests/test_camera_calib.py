"""Check camera calibration files and pinhole projection without MuJoCo, ROS or a GPU."""

import importlib.util
import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_calib import (REPO_ROOT, THREE_FROM_WORLD, frame_matches, load_calibration,
                          normalize_camera, quaternion_to_rotation, scene_calibration)
from observation_export import ground_position
from perception_feed import WorkerFusion, sensor_payload

SCENARIO_FILE = Path(__file__).resolve().parents[1] / "cameras/scenario_cctv.json"
SCENARIO_SCENE = REPO_ROOT / "control/mujoco/models/scenario_scene.xml"
HAS_MUJOCO = importlib.util.find_spec("mujoco") is not None
IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def entry(**override):
    # MuJoCo 카메라가 (1, 2, 3)에서 바로 아래를 본다.
    return {"sensor_id": "cctv_1", "width": 640, "height": 480, "position_m": [1, 2, 3],
            "rotation_cam_to_world": IDENTITY, "rotation_convention": "mujoco_camera",
            "world_frame": "mujoco_z_up", "fovy_deg": 60} | override


def assert_matrix_close(case, a, b):
    for row_a, row_b in zip(a, b):
        for x, y in zip(row_a, row_b):
            case.assertAlmostEqual(x, y, places=9)


class CameraCalibTests(unittest.TestCase):
    def test_mujoco_camera_is_kept_and_topic_defaults_from_sensor(self):
        camera = normalize_camera(entry())
        self.assertEqual(camera["topic"], "/cctv_1/image_raw")
        assert_matrix_close(self, camera["rotation_cam_to_world"], IDENTITY)
        point = ground_position([310, 200, 330, 240], 640, 480, camera)
        self.assertAlmostEqual(point["x"], 1.0)
        self.assertAlmostEqual(point["y"], 2.0)

    def test_quaternion_matches_rotation(self):
        assert_matrix_close(self, quaternion_to_rotation([1, 0, 0, 0]), IDENTITY)
        half = math.sqrt(0.5)
        assert_matrix_close(self, quaternion_to_rotation([half, 0, 0, half]),
                            [[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        no_rotation = {k: v for k, v in entry().items() if k != "rotation_cam_to_world"}
        camera = normalize_camera(no_rotation | {"quaternion_wxyz": [2, 0, 0, 0]})  # 정규화됨
        assert_matrix_close(self, camera["rotation_cam_to_world"], IDENTITY)

    def test_ros_optical_camera_is_converted(self):
        # 광학 좌표: x 오른쪽, y 아래, z 앞. 아래를 보는 카메라의 축을 월드로 적은 행렬.
        optical = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
        camera = normalize_camera(entry(rotation_cam_to_world=optical, rotation_convention="ros_optical"))
        assert_matrix_close(self, camera["rotation_cam_to_world"], IDENTITY)

    def test_threejs_world_is_converted_back(self):
        three_pos = [1, 3, -2]  # 월드 (1, 2, 3)
        three_rot = [list(row) for row in THREE_FROM_WORLD]  # THREE_FROM_WORLD @ I
        camera = normalize_camera(entry(position_m=three_pos, rotation_cam_to_world=three_rot,
                                        world_frame="threejs_y_up"))
        self.assertEqual([round(v, 9) for v in camera["position_m"]], [1, 2, 3])
        assert_matrix_close(self, camera["rotation_cam_to_world"], IDENTITY)

    def test_intrinsics_match_fovy_and_shift_with_principal_point(self):
        focal = 240 / math.tan(math.radians(30))
        box = [400, 300, 440, 380]
        by_fovy = ground_position(box, 640, 480, normalize_camera(entry()))
        same = normalize_camera({k: v for k, v in entry().items() if k != "fovy_deg"}
                                | {"intrinsics": {"fx": focal, "fy": focal, "cx": 320, "cy": 240}})
        by_k = ground_position(box, 640, 480, same)
        self.assertAlmostEqual(by_fovy["x"], by_k["x"])
        self.assertAlmostEqual(by_fovy["y"], by_k["y"])
        shifted = normalize_camera({k: v for k, v in entry().items() if k != "fovy_deg"}
                                   | {"intrinsics": {"fx": focal, "fy": focal, "cx": 420, "cy": 240}})
        self.assertAlmostEqual(ground_position(box, 640, 480, shifted)["x"], 1.0)

    def test_invalid_cameras_are_rejected(self):
        no_rotation = {k: v for k, v in entry().items() if k != "rotation_cam_to_world"}
        no_lens = {k: v for k, v in entry().items() if k != "fovy_deg"}
        cases = [entry(sensor_id=""), entry(width=0), entry(height=480.0), entry(position_m=[0, 0]),
                 entry(position_m=[0, 0, float("nan")]), no_rotation,
                 entry(quaternion_wxyz=[1, 0, 0, 0]),
                 entry(rotation_cam_to_world=[[2, 0, 0], [0, 1, 0], [0, 0, 1]]),
                 entry(rotation_cam_to_world=[[-1, 0, 0], [0, 1, 0], [0, 0, 1]]),
                 entry(rotation_convention="opencv"), entry(world_frame="unity"),
                 entry(fovy_deg=180), no_lens, entry(intrinsics={"fx": 1, "fy": 1, "cx": 0, "cy": 0}),
                 no_lens | {"intrinsics": {"fx": -1, "fy": 1, "cx": 0, "cy": 0}},
                 no_lens | {"intrinsics": {"fx": 1, "fy": 1, "cx": 0}},
                 no_lens | {"intrinsics": [1, 1, 0, 0]}, no_lens | {"intrinsics": None},
                 [entry()], "cctv_1"]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                normalize_camera(case)

    def test_axis_fields_are_required(self):
        # 빠뜨리면 MuJoCo 기본값으로 조용히 읽혀 Three.js·실제 CCTV 파일의 투영 방향이 틀어진다.
        for field in ("world_frame", "rotation_convention"):
            missing = {k: v for k, v in entry().items() if k != field}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                normalize_camera(missing)

    def test_calibration_file_is_indexed_by_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cameras.json"
            path.write_text(json.dumps({"version": 1, "cameras": [
                entry(), entry(sensor_id="cctv_2", topic="/site/cam2/image_raw")]}))
            cameras = load_calibration(path)
            self.assertEqual(sorted(cameras), ["/cctv_1/image_raw", "/site/cam2/image_raw"])
            for bad in ({"version": 2, "cameras": [entry()]}, {"version": 1, "cameras": []},
                        {"version": 1, "cameras": [entry(), entry()]}, [entry()],
                        {"version": 1, "cameras": [entry(), "cctv_2"]},
                        {"version": 1, "cameras": [{k: v for k, v in entry().items() if k != "world_frame"}]}):
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_calibration(path)

    def test_mismatched_frames_leave_sensor_degraded(self):
        # 보정과 다른 크기의 프레임은 작업자 없음으로 보내지 않아, 2초 뒤 센서가 DEGRADED가 된다.
        camera = normalize_camera(entry())
        t0 = datetime(2026, 11, 15, tzinfo=timezone.utc)
        fusion = WorkerFusion([])
        for second, size in enumerate([(640, 480), (1280, 720), (1280, 720), (1280, 720)]):
            if frame_matches(camera, *size):
                fusion.update("cctv_1", t0 + timedelta(seconds=second), [])
        self.assertTrue(frame_matches(camera, 640, 480))
        self.assertFalse(frame_matches(camera, 480, 640))
        self.assertEqual(fusion.last_frame_at("cctv_1"), t0)
        status = sensor_payload("cctv_1", camera["topic"], camera, [], fusion.last_frame_at("cctv_1"),
                                t0 + timedelta(seconds=3))["status"]
        self.assertEqual(status, "DEGRADED")
        self.assertIsNone(fusion.snapshot(t0 + timedelta(seconds=3)))

    def test_scenario_file_projects_like_threejs_copy(self):
        # 같은 카메라를 Three.js 좌표로 적은 파일도 바닥 위치가 1 mm 안에서 같아야 한다.
        for camera in load_calibration(SCENARIO_FILE).values():
            three = {**camera, "world_frame": "threejs_y_up",
                     "position_m": [sum(THREE_FROM_WORLD[i][k] * camera["position_m"][k] for k in range(3))
                                    for i in range(3)],
                     "rotation_cam_to_world": [[sum(THREE_FROM_WORLD[i][k] * camera["rotation_cam_to_world"][k][j]
                                                    for k in range(3)) for j in range(3)] for i in range(3)]}
            for box in ([300, 200, 340, 300], [100, 250, 140, 420], [560, 300, 600, 470]):
                a = ground_position(box, 640, 480, camera)
                b = ground_position(box, 640, 480, normalize_camera(three))
                with self.subTest(sensor=camera["sensor_id"], box=box):
                    self.assertLess(math.hypot(a["x"] - b["x"], a["y"] - b["y"]), 1e-3)

    @unittest.skipUnless(HAS_MUJOCO, "mujoco is not installed")
    def test_committed_scenario_file_matches_scene(self):
        # 장면 XML의 CCTV가 바뀌면 README 명령으로 파일을 다시 만들어야 한다.
        expected = scene_calibration(SCENARIO_SCENE, ["cctv_1", "cctv_2"], 640, 480)
        committed = json.loads(SCENARIO_FILE.read_text(encoding="utf-8"))
        self.assertEqual(committed["source"], "control/mujoco/models/scenario_scene.xml")
        self.assertEqual(expected["source"], committed["source"])
        for want, got in zip(expected["cameras"], committed["cameras"], strict=True):
            self.assertEqual(want["sensor_id"], got["sensor_id"])
            for a, b in zip(want["position_m"] + sum(want["rotation_cam_to_world"], []),
                            got["position_m"] + sum(got["rotation_cam_to_world"], [])):
                self.assertAlmostEqual(a, b, places=6)


if __name__ == "__main__":
    unittest.main()
