"""TIAGo 집기·놓기 (CT-07 조작). 시나리오 A: A 작업대의 부품 상자를 B 작업대로 옮긴다.

    python tiago_manipulation.py           # 화면 없이 실행하고 결과 출력
    mjpython tiago_manipulation.py --view  # 뷰어로 보기 (macOS)

구성:
- 역기구학(IK): 팔 7축 + 몸통 승강으로 손끝(grasp point)을 목표 위치·자세로 보낸다 (감쇠 최소제곱법).
- 옆에서 잡기: 접근 방향은 수평, 손가락은 수평으로 닫는다. (손가락이 세로인 자세는 작업대 높이에서 닿지 않음)
- 베이스 이동은 DiffDriveSim의 속도 명령으로 하고, 팔은 위치 제어 액추에이터 목표값을 부드럽게 보간한다.

동작은 모두 제너레이터다. 제어값을 정한 뒤 "시뮬을 dt초 진행해 달라"는 뜻으로 dt를 yield하고,
시뮬을 실제로 진행하는 쪽(run() 또는 ROS 2 브리지의 물리 타이머)이 다음 단계를 부른다.
그래서 브리지에서는 집기 도중에도 /odom·카메라 발행과 E-Stop 처리가 멈추지 않고,
E-Stop이 오면 제너레이터를 버리는 것으로 즉시 중단할 수 있다.
"""
from __future__ import annotations

import argparse
import math
import time
from concurrent.futures import ThreadPoolExecutor

import mujoco
import numpy as np

from simulation import DiffDriveSim

ARM_JOINTS = [f"arm_{i}_joint" for i in range(1, 8)] + ["torso_lift_joint"]
ARM_ACTUATORS = [f"arm_position_{i}_position" for i in range(1, 8)] + ["torso_lift_joint_position"]
GRIPPER_ACTUATORS = ["gripper_left_finger_position", "gripper_right_finger_position"]
GRIPPER_OPEN, GRIPPER_CLOSED = 0.045, 0.0
TCP_OFFSET = np.array([0.0, 0.0, 0.25])  # arm_7_link 원점 → 손가락 사이 잡는 점 (m)

# 작업대 높이(0.64~0.74 m)에서 손을 수평으로 뻗을 수 있는 거리는 베이스 중심에서 0.45~0.80 m (IK로 측정)
REACH = 0.62          # 베이스 중심 → 물체 수평 거리
PRE_GRASP = 0.12      # 물체 앞에서 멈추는 거리
LIFT = 0.10           # 집은 뒤 들어 올리는 높이
# 물체 중심보다 2 cm 위를 잡는다. 팔이 중력으로 약 8 mm 처져서, 중심 높이로 가면 손가락 아래가 작업대에 닿는다.
GRASP_ABOVE_CENTER = 0.02
CARRY = np.array([0.50, 0.0, 0.85])  # 이동 중 손끝 위치 (베이스 기준: 앞 0.5 m, 작업대보다 높은 0.85 m)


STEP_S = 1 / 60  # 한 번에 진행을 요청하는 시뮬 시간


class ManipulationError(RuntimeError):
    pass


def run(sim: DiffDriveSim, task, viewer=None):
    """동작 제너레이터를 끝까지 실행한다. 뷰어가 있으면 실시간으로 보여 준다."""
    for dt in task:
        start = time.time()
        sim.step(dt)
        if viewer is not None:
            if not viewer.is_running():
                raise SystemExit
            viewer.sync()
            time.sleep(max(0.0, dt - (time.time() - start)))


class TiagoManipulator:
    def __init__(self, sim: DiffDriveSim, log=print):
        self.sim, self.log = sim, log
        # IK 한 번에 약 0.15초가 걸린다. 다른 스레드에서 풀어 그동안에도 시뮬·콜백(E-Stop 등)이 멈추지 않게 한다.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tiago_ik")
        m = sim.model
        self.a7 = m.body("arm_7_link").id
        self.qadr = [m.joint(j).qposadr[0] for j in ARM_JOINTS]
        self.dadr = [m.joint(j).dofadr[0] for j in ARM_JOINTS]
        self.ranges = [m.jnt_range[m.joint(j).id] for j in ARM_JOINTS]
        self.arm_act = [m.actuator(a).id for a in ARM_ACTUATORS]
        self.grip_act = [m.actuator(a).id for a in GRIPPER_ACTUATORS]

    # ---- 시뮬레이션 진행 ----
    def _advance(self, duration):
        """duration초 동안 시뮬을 진행해 달라고 요청한다 (1/60초씩 나눠 yield)."""
        end = self.sim.time + duration
        while self.sim.time < end - 1e-9:
            yield min(STEP_S, end - self.sim.time)

    # ---- 손끝 ----
    def tcp(self, data=None):
        d = data or self.sim.data
        rot = d.xmat[self.a7].reshape(3, 3)
        return d.xpos[self.a7] + rot @ TCP_OFFSET, rot

    def solve_ik(self, target, approach, finger_axis=(0, 0, 1), seed=None, attempts=6, tolerance=0.005, qpos=None):
        """IK를 풀고, 실패하면 시작 자세를 조금씩 흔들어 다시 시도한다. qpos: 기준 로봇 상태 (기본: 현재)"""
        qpos = self.sim.data.qpos.copy() if qpos is None else qpos
        rng = np.random.default_rng(0)
        best = None
        for attempt in range(attempts):
            start = np.array([qpos[a] for a in self.qadr]) if seed is None else np.asarray(seed, float)
            if attempt:
                start = start + rng.normal(0, 0.4, len(start))
            q, err = self._solve_ik_once(target, approach, finger_axis, start, qpos)
            if best is None or err < best[1]:
                best = (q, err)
            if err <= tolerance:
                return q
        raise ManipulationError(f"IK 실패: 목표 {np.round(target, 3)}까지 {best[1] * 100:.1f} cm 모자람")

    def solve_ik_async(self, *args, **kwargs):
        """IK를 다른 스레드에서 풀고, 끝날 때까지 시뮬 진행을 요청하며 기다린다 (팔은 제자리 유지).

            q = yield from self.solve_ik_async(target, approach)
        """
        kwargs.setdefault("qpos", self.sim.data.qpos.copy())  # 풀이 중 시뮬이 바뀌어도 같은 기준 상태로 푼다
        future = self._pool.submit(self.solve_ik, *args, **kwargs)
        while not future.done():
            yield STEP_S
        return future.result()

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _solve_ik_once(self, target, approach, finger_axis, start, qpos, iterations=1500):
        """손끝을 target으로, 접근 방향(손 z축)을 approach로 보내는 팔·몸통 관절값.

        finger_axis는 손가락이 닫히는 방향(손 y축)과 수직인 축(손 x축)이다. 기본값 (0,0,1)이면
        손 x축이 세로라서 손가락은 수평으로 닫힌다.
        """
        m = self.sim.model
        z = np.asarray(approach, float); z /= np.linalg.norm(z)
        x = np.asarray(finger_axis, float); x -= z * (x @ z); x /= np.linalg.norm(x)
        goal_rot = np.column_stack([x, np.cross(z, x), z])
        scratch = mujoco.MjData(m)
        scratch.qpos[:] = qpos
        for adr, value, (lo, hi) in zip(self.qadr, start, self.ranges):
            scratch.qpos[adr] = np.clip(value, lo, hi)
        jacp, jacr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
        target = np.asarray(target, float)
        for _ in range(iterations):
            mujoco.mj_kinematics(m, scratch)
            mujoco.mj_comPos(m, scratch)
            pos, rot = self.tcp(scratch)
            pos_err = target - pos
            rot_err = 0.5 * sum(np.cross(rot[:, i], goal_rot[:, i]) for i in range(3))
            if np.linalg.norm(pos_err) < 1e-3 and np.linalg.norm(rot_err) < 1e-2:
                break
            mujoco.mj_jac(m, scratch, jacp, jacr, pos, self.a7)
            jac = np.vstack([jacp, 0.5 * jacr])[:, self.dadr]
            err = np.concatenate([pos_err, 0.5 * rot_err])
            dq = jac.T @ np.linalg.solve(jac @ jac.T + 1e-3 * np.eye(6), err)
            for k, (adr, (lo, hi)) in enumerate(zip(self.qadr, self.ranges)):
                scratch.qpos[adr] = np.clip(scratch.qpos[adr] + 0.5 * dq[k], lo, hi)
        err = np.linalg.norm(pos_err) + 0.05 * np.linalg.norm(rot_err)  # 자세 오차 0.1 rad ≈ 0.5 cm로 환산
        return np.array([scratch.qpos[a] for a in self.qadr]), err

    # ---- 팔·그리퍼 ----
    def move_arm(self, joint_targets, duration=1.5):
        ctrl = self.sim.data.ctrl
        start = np.array([ctrl[a] for a in self.arm_act])
        steps = max(1, int(duration * 60))
        for k in range(1, steps + 1):
            alpha = (1 - math.cos(math.pi * k / steps)) / 2
            for a, value in zip(self.arm_act, start + alpha * (joint_targets - start)):
                ctrl[a] = value
            yield from self._advance(duration / steps)
        yield from self._advance(0.3)  # 자리 잡기

    def move_tcp(self, target, approach, duration=1.5, seed=None, tolerance=0.005):
        q = yield from self.solve_ik_async(target, approach, seed=seed, tolerance=tolerance)
        yield from self.move_arm(q, duration)
        return q

    def gripper(self, opening, duration=0.8):
        for a in self.grip_act:
            self.sim.data.ctrl[a] = opening
        yield from self._advance(duration)

    # ---- 베이스 ----
    def go_to(self, x, y, yaw, speed=0.3, timeout=30.0):
        """제자리 회전 → 직진 → 최종 방향 맞추기. 오차 2 cm, 3°."""
        def turn_to(heading):
            end = self.sim.time + timeout
            while self.sim.time < end:
                err = math.atan2(math.sin(heading - self.sim.pose()[2]), math.cos(heading - self.sim.pose()[2]))
                if abs(err) < math.radians(1.5):
                    break
                self.sim.set_velocity(0.0, max(-0.6, min(0.6, 2.0 * err)))
                yield from self._advance(0.02)
            self.sim.stop()
            yield from self._advance(0.3)

        cx, cy, _ = self.sim.pose()
        if math.hypot(x - cx, y - cy) > 0.02:
            yield from turn_to(math.atan2(y - cy, x - cx))
            end = self.sim.time + timeout
            while self.sim.time < end:
                cx, cy, cyaw = self.sim.pose()
                dist = math.hypot(x - cx, y - cy)
                if dist < 0.02:
                    break
                err = math.atan2(math.sin(math.atan2(y - cy, x - cx) - cyaw), math.cos(math.atan2(y - cy, x - cx) - cyaw))
                self.sim.set_velocity(min(speed, 1.0 * dist + 0.03), 2.0 * err)
                yield from self._advance(0.02)
            self.sim.stop()
            yield from self._advance(0.3)
        yield from turn_to(yaw)

    # ---- 집기·놓기 ----
    @staticmethod
    def base_pose_for(obj_xy, approach_yaw):
        """물체를 approach_yaw 방향으로 바라보며 REACH만큼 떨어진 베이스 위치."""
        return (obj_xy[0] - REACH * math.cos(approach_yaw), obj_xy[1] - REACH * math.sin(approach_yaw), approach_yaw)

    def carry_pose(self):
        """베이스 기준 CARRY 위치로 손을 들어 올린다 (이동·회전 중 작업대와 부딪히지 않게)."""
        x, y, yaw = self.sim.pose()
        fwd = np.array([math.cos(yaw), math.sin(yaw), 0.0])
        # 정확할 필요가 없는 자세라 허용 오차를 3 cm로 둔다
        yield from self.move_tcp(np.array([x, y, 0.0]) + fwd * CARRY[0] + [0, 0, CARRY[2]], fwd, 1.2, tolerance=0.03)

    def pick(self, obj_pos, approach_yaw):
        """obj_pos(물체 중심)를 approach_yaw 방향에서 옆으로 잡아 들어 올린다."""
        obj_pos = np.asarray(obj_pos, float) + [0, 0, GRASP_ABOVE_CENTER]
        fwd = np.array([math.cos(approach_yaw), math.sin(approach_yaw), 0.0])
        self.log(f"[PICK] 베이스 이동 → {np.round(self.base_pose_for(obj_pos[:2], approach_yaw)[:2], 2)}")
        yield from self.go_to(*self.base_pose_for(obj_pos[:2], approach_yaw))
        self.log("[PICK] 그리퍼 열기·접근")
        yield from self.gripper(GRIPPER_OPEN)
        q_grasp = yield from self.solve_ik_async(obj_pos, fwd)  # 잡는 자세를 먼저 풀어 주변 자세의 시작값으로 쓴다
        yield from self.move_tcp(obj_pos - fwd * PRE_GRASP, fwd, seed=q_grasp)
        yield from self.move_arm(q_grasp, 1.0)
        self.log("[PICK] 잡기·들어 올리기")
        yield from self.gripper(GRIPPER_CLOSED, 1.0)
        yield from self.move_tcp(obj_pos + [0, 0, LIFT], fwd, 1.0)
        yield from self.carry_pose()

    def place(self, place_pos, approach_yaw):
        """들고 있는 물체를 place_pos(놓일 물체 중심)에 내려놓는다."""
        place_pos = np.asarray(place_pos, float) + [0, 0, GRASP_ABOVE_CENTER]
        fwd = np.array([math.cos(approach_yaw), math.sin(approach_yaw), 0.0])
        self.log(f"[PLACE] 베이스 이동 → {np.round(self.base_pose_for(place_pos[:2], approach_yaw)[:2], 2)}")
        yield from self.go_to(*self.base_pose_for(place_pos[:2], approach_yaw))
        self.log("[PLACE] 내려놓기")
        q_place = yield from self.solve_ik_async(place_pos + [0, 0, 0.005], fwd)
        yield from self.move_tcp(place_pos + [0, 0, LIFT], fwd, seed=q_place)
        yield from self.move_arm(q_place, 1.0)
        yield from self.gripper(GRIPPER_OPEN, 1.0)
        yield from self.move_tcp(place_pos - fwd * PRE_GRASP + [0, 0, 0.02], fwd, 1.0)
        yield from self.carry_pose()


BASE_RADIUS = 0.30  # TIAGo 베이스 반지름 약 0.27 m + 여유


def choose_approach_yaw(model, obj_xy, robot_xy, obstacle_prefix="table_"):
    """물체에 접근할 방향(yaw)을 고른다.

    0°·90°·180°·270° 중 베이스 위치(물체에서 REACH만큼 뒤)가 작업대와 겹치지 않는 방향만 후보로 두고,
    물체가 놓인 작업대의 모서리에서 물체까지 거리(팔이 작업대 위로 뻗어야 하는 깊이)가 가장 짧은 방향을 고른다.
    깊이가 같으면 지금 로봇 위치에서 가까운 쪽. 작업대는 이름이 obstacle_prefix로 시작하는 box geom이다.
    """
    boxes = []
    for g in range(model.ngeom):
        name = model.geom(g).name
        if name.startswith(obstacle_prefix) and model.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX:
            boxes.append((model.geom_pos[g][:2], model.geom_size[g][:2]))
    support = [(c, h) for c, h in boxes if abs(obj_xy[0] - c[0]) <= h[0] and abs(obj_xy[1] - c[1]) <= h[1]]
    best = None
    for yaw in (0.0, math.pi / 2, math.pi, -math.pi / 2):
        bx, by, _ = TiagoManipulator.base_pose_for(obj_xy, yaw)
        # 원(베이스)과 축 정렬 상자(작업대)의 거리
        if not all(math.hypot(max(abs(bx - c[0]) - h[0], 0), max(abs(by - c[1]) - h[1], 0)) > BASE_RADIUS
                   for c, h in boxes):
            continue
        dx, dy = round(math.cos(yaw)), round(math.sin(yaw))
        # 로봇 쪽(-접근 방향) 모서리까지의 거리
        depth = min((h[0] + dx * (obj_xy[0] - c[0])) if dx else (h[1] + dy * (obj_xy[1] - c[1]))
                    for c, h in support) if support else 0.0
        key = (round(depth, 3), math.hypot(bx - robot_xy[0], by - robot_xy[1]))
        if best is None or key < best[0]:
            best = (key, yaw)
    if best is None:
        raise ManipulationError("작업대와 부딪히지 않고 물체에 다가갈 위치가 없음")
    return best[1]


# 시나리오 A의 두 지점: (물체 중심, 바라볼 방향). 작업대 모서리에 수직으로 접근한다.
STATION_A = (np.array([1.2, 0.82, 0.64]), math.pi / 2)    # A 작업대: +y를 보고 집기
STATION_B = (np.array([1.2, -0.82, 0.64]), -math.pi / 2)  # B 작업대: -y를 보고 놓기


def scenario_a(sim: DiffDriveSim, manip: TiagoManipulator, viewer=None):
    """시나리오 A: A 작업대 상자 → B 작업대. (상자 위치는 장면에서 읽는다)"""
    m, d = sim.model, sim.data
    box = m.body("part_box").id
    start = d.xpos[box].copy()
    place = STATION_B[0].copy()
    run(sim, manip.pick(start, STATION_A[1]), viewer)
    lifted = d.xpos[box][2]
    run(sim, manip.place(place, STATION_B[1]), viewer)
    sim.step(1.0)
    final = d.xpos[box].copy()
    return start, lifted, place, final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", action="store_true", help="뷰어로 보기 (macOS: mjpython)")
    args = parser.parse_args()
    sim = DiffDriveSim("scenario")

    def demo(viewer=None):
        manip = TiagoManipulator(sim)
        t0 = time.time()
        start, lifted, place, final = scenario_a(sim, manip, viewer)
        err = np.linalg.norm(final[:2] - place[:2])
        print(f"상자 시작 {np.round(start, 3)} → 최대 높이 {lifted:.3f} m → 최종 {np.round(final, 3)}")
        print(f"B 목표와의 거리 {err * 100:.1f} cm, 상자 높이 {final[2]:.3f} m (작업대 위 0.64)")
        print(f"시뮬 {sim.time:.1f} s (계산 {time.time() - t0:.1f} s)")
        ok = err < 0.05 and abs(final[2] - 0.64) < 0.01
        print("성공" if ok else "실패")
        return ok

    if args.view:
        import mujoco.viewer
        with mujoco.viewer.launch_passive(sim.model, sim.data) as viewer:
            ok = demo(viewer)
            while viewer.is_running():
                viewer.sync()
                time.sleep(0.05)
    else:
        ok = demo()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
