"""바퀴 두 개 모바일 로봇(차동 구동) MuJoCo 시뮬레이션.

    python simulation.py                  # 시나리오 장면의 TIAGo 전진·회전·정지 시험 (화면 없음)
    python simulation.py --robot tiago    # TIAGo만 있는 장면
    python simulation.py --robot simple   # 단순 시험용 바퀴 로봇
    mjpython simulation.py --view         # 뷰어로 보기 (macOS는 mjpython 필요)

ROS 2 브리지(ros2_ws/src/mujoco_bridge)도 이 모듈의 DiffDriveSim을 사용한다.
"""
import argparse
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import mujoco
import mujoco.viewer
import numpy as np

MODELS = Path(__file__).resolve().parent / "models"


def add_tiago_finger_pads(spec: "mujoco.MjSpec") -> None:
    """TIAGo 손가락에 평평한 접촉 패드를 붙이고, 손가락 메시는 보이기만 하게 바꾼다.

    MuJoCo는 메시 충돌에 볼록 껍질(convex hull)을 쓴다. TIAGo 손가락 메시는 좁은 뿌리와 넓은 패드가
    이어져 있어 껍질의 안쪽 면이 쐐기처럼 기울고, 손가락을 닫으면 물체가 손끝 쪽으로 밀려 빠진다.
    그래서 실제 패드 위치(손가락 좌표 z -0.218~-0.129, 안쪽 면 x=0)에 얇은 상자를 붙여 충돌을 맡긴다.
    원본 tiago.xml은 수정하지 않는다.
    """
    for name, outward in (("gripper_left_finger_link", -1.0), ("gripper_right_finger_link", 1.0)):
        body = spec.body(name)
        for geom in body.geoms:
            geom.contype, geom.conaffinity = 0, 0
        body.add_geom(name=f"{name}_pad", type=mujoco.mjtGeom.mjGEOM_BOX,
                      size=[0.004, 0.02, 0.045], pos=[outward * 0.004, -0.0016, -0.172],
                      contype=1, conaffinity=1, condim=4, friction=[1.5, 0.01, 0.001],
                      mass=0.0, group=3, rgba=[0.15, 0.15, 0.15, 1])


@dataclass(frozen=True)
class RobotSpec:
    """차동 구동 로봇을 움직이는 데 필요한 모델 내 이름과 바퀴 반지름."""
    model_path: Path
    base_joint: str
    left_motor: str
    right_motor: str
    left_wheel_body: str
    right_wheel_body: str
    wheel_radius: float
    keyframe: str | None = None
    edit: Callable | None = None  # 모델을 컴파일하기 전에 MjSpec을 고치는 함수


ROBOTS = {
    # 시나리오 장면: TIAGo + 금지구역 + A·B 작업대와 상자 + CCTV 2대 + 머리 카메라 + 작업자 (scenario.py 참고)
    "scenario": RobotSpec(MODELS / "scenario_scene.xml", "reference", "wheel_left_joint_vel", "wheel_right_joint_vel",
                          "wheel_left_link", "wheel_right_link", wheel_radius=0.0985, keyframe="home",
                          edit=add_tiago_finger_pads),
    # PAL TIAGo: 바퀴 + 7축 팔 + 그리퍼 (MuJoCo Menagerie). 바퀴 반지름은 원본 URDF 값.
    "tiago": RobotSpec(MODELS / "tiago_scene.xml", "reference", "wheel_left_joint_vel", "wheel_right_joint_vel",
                       "wheel_left_link", "wheel_right_link", wheel_radius=0.0985, keyframe="home",
                       edit=add_tiago_finger_pads),
    # 단순 시험용 바퀴 로봇
    "simple": RobotSpec(MODELS / "diff_drive.xml", "base_joint", "left_wheel_motor", "right_wheel_motor",
                        "left_wheel", "right_wheel", wheel_radius=0.05),
}
DEFAULT_ROBOT = "scenario"


class DiffDriveSim:
    """선속도(m/s)·각속도(rad/s) 명령을 좌우 바퀴 각속도로 바꿔 로봇을 움직인다."""

    def __init__(self, robot: str = DEFAULT_ROBOT, model_path=None):
        self.spec = spec = ROBOTS[robot]
        path = str(model_path or spec.model_path)
        if spec.edit:
            mj_spec = mujoco.MjSpec.from_file(path)
            spec.edit(mj_spec)
            self.model = mj_spec.compile()
        else:
            self.model = mujoco.MjModel.from_xml_path(path)
        self.data = mujoco.MjData(self.model)
        m = self.model
        if spec.keyframe:
            mujoco.mj_resetDataKeyframe(m, self.data, m.key(spec.keyframe).id)
        self.wheel_radius = spec.wheel_radius
        self.wheel_separation = float(np.linalg.norm(
            m.body(spec.left_wheel_body).pos[:2] - m.body(spec.right_wheel_body).pos[:2]))
        self.left = m.actuator(spec.left_motor).id
        self.right = m.actuator(spec.right_motor).id
        self.max_wheel_speed = float(min(abs(m.actuator_ctrlrange[self.left]).max(),
                                         abs(m.actuator_ctrlrange[self.right]).max()))
        joint = m.joint(spec.base_joint)
        self.qpos_adr, self.qvel_adr = joint.qposadr[0], joint.dofadr[0]
        self.command = (0.0, 0.0)
        mujoco.mj_forward(m, self.data)

    @property
    def time(self) -> float:
        return self.data.time

    def set_velocity(self, linear: float, angular: float) -> None:
        half = angular * self.wheel_separation / 2
        self.data.ctrl[self.left] = (linear - half) / self.wheel_radius
        self.data.ctrl[self.right] = (linear + half) / self.wheel_radius
        self.command = (linear, angular)

    def stop(self) -> None:
        self.set_velocity(0.0, 0.0)

    def step(self, duration: float) -> None:
        for _ in range(max(1, round(duration / self.model.opt.timestep))):
            mujoco.mj_step(self.model, self.data)

    def pose(self):
        """(x, y, yaw) — world 좌표, yaw는 rad."""
        x, y, _, qw, qx, qy, qz = self.data.qpos[self.qpos_adr:self.qpos_adr + 7]
        yaw = math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
        return float(x), float(y), yaw

    def orientation(self):
        """(x, y, z, w) 쿼터니언."""
        qw, qx, qy, qz = self.data.qpos[self.qpos_adr + 3:self.qpos_adr + 7]
        return float(qx), float(qy), float(qz), float(qw)

    def twist(self):
        """(전진 속도 m/s, 회전 속도 rad/s) — 로봇 기준."""
        vx, vy = self.data.qvel[self.qvel_adr:self.qvel_adr + 2]
        _, _, yaw = self.pose()
        forward = vx * math.cos(yaw) + vy * math.sin(yaw)
        return float(forward), float(self.data.qvel[self.qvel_adr + 5])


def run_demo(sim: DiffDriveSim, viewer=None):
    """STEP 4 시험: 전진 → 회전 → 정지."""

    def hold(duration):
        if viewer is None:
            sim.step(duration)
            return
        end = sim.time + duration
        while sim.time < end and viewer.is_running():
            start = time.time()
            sim.step(1 / 60)
            viewer.sync()
            time.sleep(max(0.0, 1 / 60 - (time.time() - start)))

    checks = []
    x0, _, _ = sim.pose()
    sim.set_velocity(0.3, 0.0)
    hold(2.0)
    x1, y1, yaw1 = sim.pose()
    checks.append(("전진 0.3 m/s × 2 s", abs((x1 - x0) - 0.6) < 0.06, f"이동 {x1 - x0:.3f} m (목표 0.6)"))

    sim.set_velocity(0.0, 0.5)
    hold(math.pi / 2 / 0.5)
    _, _, yaw2 = sim.pose()
    turned = math.degrees(yaw2 - yaw1)
    checks.append(("제자리 회전 0.5 rad/s", abs(turned - 90) < 10, f"회전 {turned:.1f}° (목표 90)"))

    sim.stop()
    hold(1.0)
    v, w = sim.twist()
    checks.append(("정지", abs(v) < 0.01 and abs(w) < 0.01, f"속도 {v:.4f} m/s, {w:.4f} rad/s"))

    x, y, yaw = sim.pose()
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"최종 위치 x={x:.3f} y={y:.3f} yaw={math.degrees(yaw):.1f}°")
    return all(ok for _, ok, _ in checks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", choices=sorted(ROBOTS), default=DEFAULT_ROBOT)
    parser.add_argument("--view", action="store_true", help="뷰어로 보기 (macOS: mjpython)")
    args = parser.parse_args()
    sim = DiffDriveSim(args.robot)
    print(f"로봇: {args.robot} (바퀴 반지름 {sim.wheel_radius} m, 바퀴 간격 {sim.wheel_separation:.4f} m)")
    if args.view:
        with mujoco.viewer.launch_passive(sim.model, sim.data) as viewer:
            ok = run_demo(sim, viewer)
            while viewer.is_running():
                viewer.sync()
                time.sleep(0.05)
    else:
        ok = run_demo(sim)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
