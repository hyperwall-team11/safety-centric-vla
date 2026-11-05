"""Check camera calibration files and pinhole projection without MuJoCo, ROS or a GPU."""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_calib import (THREE_FROM_WORLD, load_calibration, normalize_camera,
                          quaternion_to_rotation)
from observation_export import ground_position

IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def entry(**override):
    # MuJoCo 카메라가 (1, 2, 3)에서 바로 아래를 본다.
    return {"sensor_id": "cctv_1", "width": 640, "height": 480, "position_m": [1, 2, 3],
            "rotation_cam_to_world": IDENTITY, "fovy_deg": 60} | override


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
                 no_lens | {"intrinsics": {"fx": 1, "fy": 1, "cx": 0}}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                normalize_camera(case)

    def test_calibration_file_is_indexed_by_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cameras.json"
            path.write_text(json.dumps({"version": 1, "cameras": [
                entry(), entry(sensor_id="cctv_2", topic="/site/cam2/image_raw")]}))
            cameras = load_calibration(path)
            self.assertEqual(sorted(cameras), ["/cctv_1/image_raw", "/site/cam2/image_raw"])
            for bad in ({"version": 2, "cameras": [entry()]}, {"version": 1, "cameras": []},
                        {"version": 1, "cameras": [entry(), entry()]}):
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    load_calibration(path)


if __name__ == "__main__":
    unittest.main()
