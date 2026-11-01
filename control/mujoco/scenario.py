"""시나리오 장면(scenario_scene.xml) 부가 기능: 작업자 접근 동작, 카메라 렌더·내부 파라미터, 관절 목록.

ROS 2에 의존하지 않으므로 브리지와 테스트, 스냅샷 스크립트에서 함께 쓴다.

    python scenario.py --snapshot out/   # 카메라 3대 이미지를 PNG로 저장 (작업자가 금지구역 안에 있는 시점)
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

# 스키마 v1.1 frame_id 규칙: 영상 좌표계는 camera_<sensor_id>
CAMERAS = {
    # sensor_id: (MuJoCo 카메라 이름, ROS 2 토픽 접두어, 탑재 위치)
    "cctv_1": ("cctv_1", "/cctv_1", "world"),
    "cctv_2": ("cctv_2", "/cctv_2", "world"),
    "head_camera": ("head_camera", "/robot/head_camera", "base_link"),
}

# /joint_states로 내보낼 TIAGo 관절 (팔·몸통·머리·그리퍼·바퀴). 보조바퀴(caster)는 수동 관절이라 제외.
JOINT_STATE_NAMES = [
    *(f"arm_{i}_joint" for i in range(1, 8)),
    "torso_lift_joint",
    "head_1_joint", "head_2_joint",
    "gripper_left_finger_joint", "gripper_right_finger_joint",
    "wheel_left_joint", "wheel_right_joint",
]

# MuJoCo 카메라(-z 방향을 보고 +y가 위)를 ROS 광학 좌표계(+z 앞, +x 오른쪽, +y 아래)로 바꾸는 회전
MJ_TO_OPTICAL = np.diag([1.0, -1.0, -1.0])


@dataclass
class WorkerScript:
    """작업자 mocap 바디를 경로점을 따라 일정 속도로 움직인다. 경로는 끝나면 처음부터 반복한다.

    waypoints: (x, y, 머무는 시간 s). 기본 경로는 금지구역 바깥 → 금지구역 안(2초 정지) → 바깥.
    """
    waypoints: list[tuple[float, float, float]] = field(default_factory=lambda: [
        (3.4, 1.8, 1.0),
        (1.6, 0.15, 2.0),   # 금지구역(x 0.5~1.9, y -0.6~0.6) 안
        (3.4, -1.8, 1.0),
    ])
    speed: float = 0.6  # m/s
    start_offset: float = 0.0

    def __post_init__(self):
        self._segments = []  # (시작 시각, 길이 시간, 시작점, 끝점, 머무름 여부)
        t = 0.0
        n = len(self.waypoints)
        for i, (x, y, dwell) in enumerate(self.waypoints):
            here = np.array([x, y])
            nxt = np.array(self.waypoints[(i + 1) % n][:2])
            if dwell > 0:
                self._segments.append((t, dwell, here, here))
                t += dwell
            travel = float(np.linalg.norm(nxt - here)) / self.speed
            if travel > 0:
                self._segments.append((t, travel, here, nxt))
                t += travel
        self.period = t

    def position(self, sim_time: float) -> tuple[float, float, float]:
        """(x, y, 진행 방향 yaw rad)"""
        t = (sim_time + self.start_offset) % self.period
        for start, duration, a, b in self._segments:
            if t < start + duration:
                alpha = (t - start) / duration
                p = a + alpha * (b - a)
                heading = self._heading(a, b)
                return float(p[0]), float(p[1]), heading
        a, b = self._segments[-1][2:]
        return float(b[0]), float(b[1]), self._heading(a, b)

    def _heading(self, a, b):
        if np.allclose(a, b):
            # 머무는 구간은 다음 이동 방향을 바라본다
            idx = next(i for i, s in enumerate(self._segments) if s[2] is a and s[3] is b)
            for _, _, na, nb in self._segments[idx + 1:] + self._segments[:idx]:
                if not np.allclose(na, nb):
                    return math.atan2(nb[1] - na[1], nb[0] - na[0])
            return 0.0
        return math.atan2(b[1] - a[1], b[0] - a[0])

    def apply(self, model, data) -> tuple[float, float, float]:
        x, y, yaw = self.position(data.time)
        mocap = model.body_mocapid[model.body("worker").id]
        data.mocap_pos[mocap] = [x, y, 0.0]
        data.mocap_quat[mocap] = [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]
        return x, y, yaw


def in_zone(model, geom_name: str, x: float, y: float) -> bool:
    """(x, y)가 바닥에 깔린 box geom(예: keep_out_zone) 안에 있는지. 회전 없는 구역 기준."""
    g = model.geom(geom_name)
    cx, cy = g.pos[:2]
    hx, hy = g.size[:2]
    return bool(abs(x - cx) <= hx and abs(y - cy) <= hy)


def camera_intrinsics(model, camera: str, width: int, height: int):
    """(fx, fy, cx, cy). MuJoCo fovy는 세로 시야각(도)."""
    fovy = math.radians(model.cam_fovy[model.camera(camera).id])
    fy = (height / 2) / math.tan(fovy / 2)
    return fy, fy, width / 2, height / 2


def camera_pose_world(data, model, camera: str):
    """카메라 광학 좌표계의 world 기준 (위치, 3x3 회전)."""
    cid = model.camera(camera).id
    rot = data.cam_xmat[cid].reshape(3, 3) @ MJ_TO_OPTICAL
    return data.cam_xpos[cid].copy(), rot


def relative_pose(parent_pos, parent_rot, child_pos, child_rot):
    """parent 좌표계 기준 child의 (위치, 회전)."""
    return parent_rot.T @ (child_pos - parent_pos), parent_rot.T @ child_rot


def rot_to_quat(rot) -> tuple[float, float, float, float]:
    """3x3 회전 → (x, y, z, w)."""
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.asarray(rot, dtype=float).reshape(9))
    w, x, y, z = q
    return float(x), float(y), float(z), float(w)


class CameraRig:
    """카메라 여러 대를 하나의 렌더러로 그린다. 렌더러는 생성한 스레드에서만 써야 한다 (OpenGL 컨텍스트)."""

    def __init__(self, model, width=640, height=480, sensors=tuple(CAMERAS)):
        self.model, self.width, self.height = model, width, height
        self.sensors = list(sensors)
        self.renderer = mujoco.Renderer(model, height=height, width=width)

    def render(self, data, sensor_id: str) -> np.ndarray:
        self.renderer.update_scene(data, camera=CAMERAS[sensor_id][0])
        return self.renderer.render()

    def close(self):
        self.renderer.close()


def snapshot(out_dir: Path, sim_time: float | None = None):
    """작업자가 금지구역 안에 있는 시점의 카메라 3대 이미지를 저장한다."""
    from PIL import Image

    from simulation import DiffDriveSim

    sim = DiffDriveSim("scenario")
    worker = WorkerScript()
    if sim_time is None:
        # 첫 번째 '금지구역 안 정지' 구간의 가운데
        start, duration, *_ = next(s for s in worker._segments
                                   if np.allclose(s[2], s[3]) and in_zone(sim.model, "keep_out_zone", *s[2]))
        sim_time = start + duration / 2
    # 로봇은 상자 쪽을 바라보도록 조금 회전
    sim.set_velocity(0.0, 0.4)
    while sim.time < sim_time:
        worker.apply(sim.model, sim.data)
        sim.step(0.02)
        if sim.time > 1.2:
            sim.stop()
    out_dir.mkdir(parents=True, exist_ok=True)
    rig = CameraRig(sim.model)
    x, y, _ = worker.apply(sim.model, sim.data)
    mujoco.mj_forward(sim.model, sim.data)
    paths = []
    for sensor in rig.sensors:
        path = out_dir / f"{sensor}.png"
        Image.fromarray(rig.render(sim.data, sensor)).save(path)
        paths.append(path)
    rig.close()
    inside = in_zone(sim.model, "keep_out_zone", x, y)
    print(f"t={sim.time:.2f}s 작업자=({x:.2f}, {y:.2f}) 금지구역 안={inside}")
    for path in paths:
        print(f"저장: {path}")
    return paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True, help="PNG 저장 폴더")
    parser.add_argument("--time", type=float, default=None, help="시뮬 시각 (기본: 작업자가 금지구역 안)")
    args = parser.parse_args()
    snapshot(args.snapshot, args.time)


if __name__ == "__main__":
    main()
