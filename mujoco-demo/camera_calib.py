"""Load fixed-CCTV calibration from a JSON file, or write one from a MuJoCo scene.

With a calibration file the CV node can project detections for any camera source (MuJoCo,
Three.js or a real CCTV) without reading the simulator. The file describes each camera in the
backend world frame; the result is the dictionary ``observation_export.ground_position`` uses.

    {"version": 1, "cameras": [{
        "sensor_id": "cctv_1", "topic": "/cctv_1/image_raw", "width": 640, "height": 480,
        "position_m": [1.2, -4.2, 3.2],
        "rotation_cam_to_world": [[...], [...], [...]],   # or "quaternion_wxyz": [w, x, y, z]
        "rotation_convention": "mujoco_camera",           # or "ros_optical"
        "world_frame": "mujoco_z_up",                      # or "threejs_y_up"
        "fovy_deg": 60,                                    # or "intrinsics": {"fx", "fy", "cx", "cy"}
        "ground_plane_z_m": 0.0}]}

Lens distortion is not modelled; undistort images before detection if the lens needs it.

    python camera_calib.py --scene ../control/mujoco/models/scenario_scene.xml \
        --camera cctv_1 --camera cctv_2 --output cameras.json
"""

import argparse
import json
import math
from pathlib import Path

from observation_export import ROTATION_CONVENTION

ROTATION_TOLERANCE = 1e-3
# Three.js(y 위)의 점 p_three = THREE_FROM_WORLD @ p_world. 월드는 MuJoCo·백엔드 좌표(z 위)다.
THREE_FROM_WORLD = ((1, 0, 0), (0, 0, 1), (0, -1, 0))
# ROS 광학 좌표(x 오른쪽, y 아래, z 앞)를 MuJoCo 카메라 좌표(x 오른쪽, y 위, -z 앞)로 바꾸는 축 뒤집기.
OPTICAL_TO_MUJOCO = (1, -1, -1)


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _finite_numbers(values, count, name):
    if not isinstance(values, (list, tuple)) or len(values) != count:
        raise ValueError(f"{name} needs {count} numbers")
    numbers = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must contain finite numbers")
        numbers.append(float(value))
    return numbers


def quaternion_to_rotation(wxyz):
    w, x, y, z = _finite_numbers(wxyz, 4, "quaternion_wxyz")
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-9:
        raise ValueError("quaternion_wxyz must not be zero")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def _check_rotation(rotation):
    product = _matmul(_transpose(rotation), rotation)
    off = max(abs(product[i][j] - (i == j)) for i in range(3) for j in range(3))
    det = (rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
           - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
           + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0]))
    if off > ROTATION_TOLERANCE or abs(det - 1) > ROTATION_TOLERANCE:
        raise ValueError("rotation must be a proper rotation matrix")


def normalize_camera(entry):
    """Validated camera in the MuJoCo convention and backend world frame (z up)."""
    sensor_id = entry.get("sensor_id")
    if not isinstance(sensor_id, str) or not sensor_id:
        raise ValueError("Each camera needs a sensor_id")
    width, height = entry.get("width"), entry.get("height")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        raise ValueError(f"{sensor_id}: width and height must be positive integers")
    position = _finite_numbers(entry.get("position_m"), 3, f"{sensor_id} position_m")
    if ("rotation_cam_to_world" in entry) == ("quaternion_wxyz" in entry):
        raise ValueError(f"{sensor_id}: give exactly one of rotation_cam_to_world or quaternion_wxyz")
    if "quaternion_wxyz" in entry:
        rotation = quaternion_to_rotation(entry["quaternion_wxyz"])
    else:
        rows = entry["rotation_cam_to_world"]
        if not isinstance(rows, (list, tuple)) or len(rows) != 3:
            raise ValueError(f"{sensor_id}: rotation_cam_to_world must be 3x3")
        rotation = [_finite_numbers(row, 3, f"{sensor_id} rotation row") for row in rows]
    _check_rotation(rotation)

    convention = entry.get("rotation_convention", ROTATION_CONVENTION)
    if convention == "ros_optical":
        rotation = [[rotation[i][j] * OPTICAL_TO_MUJOCO[j] for j in range(3)] for i in range(3)]
    elif convention != ROTATION_CONVENTION:
        raise ValueError(f"{sensor_id}: unknown rotation_convention {convention!r}")

    world = entry.get("world_frame", "mujoco_z_up")
    if world == "threejs_y_up":
        back = _transpose([list(row) for row in THREE_FROM_WORLD])
        position = [sum(back[i][k] * position[k] for k in range(3)) for i in range(3)]
        rotation = _matmul(back, rotation)
    elif world != "mujoco_z_up":
        raise ValueError(f"{sensor_id}: unknown world_frame {world!r}")

    camera = {"sensor_id": sensor_id, "topic": entry.get("topic") or f"/{sensor_id}/image_raw",
              "width": width, "height": height, "position_m": position,
              "rotation_cam_to_world": rotation, "rotation_convention": ROTATION_CONVENTION,
              "ground_plane_z_m": _finite_numbers([entry.get("ground_plane_z_m", 0.0)], 1,
                                                   f"{sensor_id} ground_plane_z_m")[0]}
    if ("fovy_deg" in entry) == ("intrinsics" in entry):
        raise ValueError(f"{sensor_id}: give exactly one of fovy_deg or intrinsics")
    if "fovy_deg" in entry:
        fovy = _finite_numbers([entry["fovy_deg"]], 1, f"{sensor_id} fovy_deg")[0]
        if not 0 < fovy < 180:
            raise ValueError(f"{sensor_id}: fovy_deg must be between 0 and 180")
        camera["fovy_deg"] = fovy
    else:
        k = entry["intrinsics"] or {}
        fx, fy, cx, cy = _finite_numbers([k.get(n) for n in ("fx", "fy", "cx", "cy")], 4,
                                         f"{sensor_id} intrinsics")
        if fx <= 0 or fy <= 0:
            raise ValueError(f"{sensor_id}: fx and fy must be positive")
        camera["intrinsics"] = {"fx": fx, "fy": fy, "cx": cx, "cy": cy}
    return camera


def load_calibration(path):
    """Cameras by topic from a calibration file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("cameras"), list) or not data["cameras"]:
        raise ValueError("Calibration file needs version 1 and a non-empty cameras list")
    cameras = {}
    for entry in data["cameras"]:
        camera = normalize_camera(entry)
        if camera["topic"] in cameras:
            raise ValueError(f"Duplicate camera topic {camera['topic']}")
        cameras[camera["topic"]] = camera
    return cameras


def scene_calibration(scene_path, sensor_ids, width, height):
    """Calibration entries for world-fixed cameras of a MuJoCo scene."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    entries = []
    for sensor_id in sensor_ids:
        cam = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, sensor_id)
        if cam < 0:
            raise ValueError(f"Camera {sensor_id} is not in {scene_path}")
        if model.cam_bodyid[cam] != 0:
            raise ValueError(f"Camera {sensor_id} moves with a body; only fixed CCTVs are supported")
        entries.append({"sensor_id": sensor_id, "topic": f"/{sensor_id}/image_raw",
                        "width": width, "height": height,
                        "position_m": [float(v) for v in data.cam_xpos[cam]],
                        "rotation_cam_to_world": [[float(v) for v in row]
                                                  for row in data.cam_xmat[cam].reshape(3, 3)],
                        "rotation_convention": ROTATION_CONVENTION, "world_frame": "mujoco_z_up",
                        "fovy_deg": float(model.cam_fovy[cam]), "ground_plane_z_m": 0.0})
    return {"version": 1, "source": str(scene_path), "cameras": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--camera", action="append", required=True, help="scene camera name; repeat")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration = scene_calibration(args.scene, args.camera, args.width, args.height)
    for entry in calibration["cameras"]:
        normalize_camera(entry)
    args.output.write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(calibration['cameras'])} cameras to {args.output}")


if __name__ == "__main__":
    main()
