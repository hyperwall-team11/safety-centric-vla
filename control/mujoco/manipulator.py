"""Franka Panda를 MuJoCo에서 움직이는 기본 동작(이동, 그리퍼 열기·닫기)과 시나리오 1 예제.

    python robot_actions.py            # 화면 없이 실행하고 결과만 출력
    mjpython robot_actions.py --view   # 뷰어로 동작 확인 (macOS는 mjpython 필요)
"""
import argparse
import time
from pathlib import Path

import mujoco
import mujoco.viewer
import numpy as np

SCENE = Path(__file__).resolve().parent / "models" / "panda_scene.xml"
ARM_JOINTS = [f"joint{i}" for i in range(1, 8)]
GRIPPER_OPEN, GRIPPER_CLOSED = 255.0, 0.0
TCP_OFFSET = np.array([0.0, 0.0, 0.1034])  # hand 원점에서 손가락 끝까지 (m)


class PandaRobot:
    def __init__(self, model, data, viewer=None):
        self.model, self.data, self.viewer = model, data, viewer
        self.qpos_ids = [model.jnt_qposadr[model.joint(j).id] for j in ARM_JOINTS]
        self.dof_ids = [model.jnt_dofadr[model.joint(j).id] for j in ARM_JOINTS]
        self.hand_id = model.body("hand").id
        self.gripper = GRIPPER_OPEN
        mujoco.mj_forward(model, data)
        self.down_rot = data.xmat[self.hand_id].reshape(3, 3).copy()  # 손이 아래를 향한 자세

    def tcp_position(self, data=None):
        data = data or self.data
        rot = data.xmat[self.hand_id].reshape(3, 3)
        return data.xpos[self.hand_id] + rot @ TCP_OFFSET

    def solve_ik(self, target, iterations=200, damping=1e-3):
        """손 끝을 target(월드 좌표, m)으로 보내는 관절각. 자세는 아래 방향으로 유지."""
        scratch = mujoco.MjData(self.model)
        scratch.qpos[:] = self.data.qpos
        jacp, jacr = np.zeros((3, self.model.nv)), np.zeros((3, self.model.nv))
        for _ in range(iterations):
            mujoco.mj_kinematics(self.model, scratch)
            mujoco.mj_comPos(self.model, scratch)
            pos_err = target - self.tcp_position(scratch)
            rot = scratch.xmat[self.hand_id].reshape(3, 3)
            rot_err = 0.5 * sum(np.cross(rot[:, i], self.down_rot[:, i]) for i in range(3))
            err = np.concatenate([pos_err, rot_err])
            if np.linalg.norm(err) < 1e-4:
                break
            mujoco.mj_jac(self.model, scratch, jacp, jacr, self.tcp_position(scratch), self.hand_id)
            jac = np.vstack([jacp, jacr])[:, self.dof_ids]
            dq = jac.T @ np.linalg.solve(jac @ jac.T + damping * np.eye(6), err)
            for qid, step in zip(self.qpos_ids, dq):
                scratch.qpos[qid] += step
            for i, j in enumerate(ARM_JOINTS):
                lo, hi = self.model.jnt_range[self.model.joint(j).id]
                scratch.qpos[self.qpos_ids[i]] = np.clip(scratch.qpos[self.qpos_ids[i]], lo, hi)
        return scratch.qpos[self.qpos_ids].copy()

    def _run(self, arm_target, duration):
        start = self.data.ctrl[:7].copy()
        steps = max(1, int(duration / self.model.opt.timestep))
        for k in range(steps):
            alpha = (1 - np.cos(np.pi * (k + 1) / steps)) / 2  # 부드럽게 가속·감속
            self.data.ctrl[:7] = start + alpha * (arm_target - start)
            self.data.ctrl[7] = self.gripper
            mujoco.mj_step(self.model, self.data)
            if self.viewer is not None and k % 8 == 0:
                if not self.viewer.is_running():
                    raise SystemExit
                self.viewer.sync()
                time.sleep(8 * self.model.opt.timestep)

    def move_to(self, xyz, duration=1.5):
        self._run(self.solve_ik(np.asarray(xyz, dtype=float)), duration)

    def open_gripper(self, duration=0.6):
        self.gripper = GRIPPER_OPEN
        self._run(self.data.ctrl[:7].copy(), duration)

    def close_gripper(self, duration=0.8):
        self.gripper = GRIPPER_CLOSED
        self._run(self.data.ctrl[:7].copy(), duration)


def pick_and_place(robot, pick, place, lift=0.2):
    """시나리오 1: 접근 → 파지 → 이동 → 놓기."""
    pick, place = np.asarray(pick), np.asarray(place)
    up = np.array([0, 0, lift])
    robot.open_gripper()
    robot.move_to(pick + up)
    robot.move_to(pick)
    robot.close_gripper()
    robot.move_to(pick + up)
    robot.move_to(place + up, duration=2.0)
    robot.move_to(place)
    robot.open_gripper()
    robot.move_to(place + up)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", action="store_true", help="뷰어로 동작 보기 (macOS: mjpython)")
    args = parser.parse_args()

    model = mujoco.MjModel.from_xml_path(str(SCENE))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, model.key("home").id)

    box = model.body("part_box").id
    pick = model.site("zone_a").pos + [0, 0, 0.025]
    place = model.site("zone_b").pos + [0, 0, 0.025]

    def run(viewer=None):
        robot = PandaRobot(model, data, viewer)
        pick_and_place(robot, pick, place)
        final = data.xpos[box]
        dist = np.linalg.norm(final[:2] - model.site("zone_b").pos[:2])
        print(f"상자 최종 위치: {np.round(final, 3)}  B구역 중심과의 거리: {dist * 100:.1f} cm")
        print("성공" if dist < 0.05 else "실패")

    if args.view:
        with mujoco.viewer.launch_passive(model, data) as viewer:
            run(viewer)
            while viewer.is_running():
                viewer.sync()
                time.sleep(0.05)
    else:
        run()


if __name__ == "__main__":
    main()
