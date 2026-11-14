"""공장 현장(팔 4대 + AMR 2대)을 ROS 2 없이 정해진 순서로 움직이는 데모 스크립트. 오프라인 렌더용이다.

ROS 2 실행에서는 어댑터(arm_adapter·amr_adapter)와 순환 데모(arm_cycle_demo·amr_cycle_demo)가 같은 동작을 낸다.
여기서는 같은 궤적 계획(arms.plan_manipulation)과 같은 AMR 운동학(amr.PlantAmrs)을 시간에 따라 바로 재생한다.

- 팔: PICK(랙·포장대) → PLACE → 반대 방향 PICK → PLACE를 반복. 관절각을 그대로 넣는다(운동학 재생).
- AMR: 창고 거치대 PICK → 작업 셀로 MOVE_TO → PLACE → PICK → 창고로 MOVE_TO → PLACE를 반복.

    motion = PlantMotion(model, data, kins, plant_amrs)
    while ...:
        sim.step(dt)
        motion.step(data, sim.time, dt)
"""
from __future__ import annotations

import bisect
import math

import mujoco
import numpy as np

import amr
import arms

ARM_PAUSE_S = 1.0  # 동작 사이 쉬는 시간
ARM_SPEED = 0.25  # TCP 속도 (arm_core.DEFAULT_TCP_SPEED와 같음)
AMR_SPEED = 0.4  # SR-01 기본값 (amr_core.DEFAULT_MAX_SPEED와 같음)
AMR_START_S = (1.0, 5.0)  # 2대가 엇갈려 움직이게


class ArmReplay:
    """팔 한 대의 반복 궤적. 시작할 때 네 동작(PICK·PLACE·PICK·PLACE)을 미리 계획한다."""

    def __init__(self, model, data, kin: arms.ArmKinematics, layout: arms.PlantArm, offset: float):
        self.kin, self.offset = kin, offset
        self.box = model.body_mocapid[model.body(f"box:{layout.robot_id}").id]
        self.times, self.qs, self.holding = [], [], []
        q, t = kin.get(data), 0.0
        steps = (("PICK", layout.pick), ("PLACE", layout.place), ("PICK", layout.place), ("PLACE", layout.pick))
        for manipulation, target in steps:
            plan = arms.plan_manipulation(kin, data, q, manipulation, target, ARM_SPEED)
            for p in plan.points:
                self.times.append(t + p.t)
                self.qs.append(np.array(p.q))
                self.holding.append(p.holding)
            t += plan.duration + ARM_PAUSE_S
            q = plan.points[-1].q
        self.period = t

    def apply(self, model, data, sim_time):
        tt = (sim_time + self.offset) % self.period
        i = min(bisect.bisect_left(self.times, tt), len(self.times) - 1)
        q = self.qs[i]
        self.kin.set(data, q)
        data.qvel[self.kin.dof_ids] = 0.0
        self.kin.command(data, q)
        if self.holding[i]:
            mujoco.mj_kinematics(model, data)
            data.mocap_pos[self.box] = self.kin.tcp(data)


class AmrScript:
    """AMR 한 대의 단계 반복. 단계가 끝나면(싣기 결과·도착) 다음 단계로 넘어간다."""

    def __init__(self, layout: amr.PlantAmr, start_at: float):
        self.layout, self.start_at = layout, start_at
        a = layout
        self.steps = [("PICK", a.warehouse_stand), ("MOVE", a.cell_dock), ("PLACE", a.cell_stand),
                      ("PICK", a.cell_stand), ("MOVE", a.warehouse_dock), ("PLACE", a.warehouse_stand)]
        self.index, self.busy, self.wait_until = 0, False, start_at

    def step(self, plant: amr.PlantAmrs, data, sim_time, results):
        rid = self.layout.robot_id
        if sim_time < self.wait_until:
            return
        kind, where = self.steps[self.index]
        if kind == "MOVE":
            x, y, yaw = plant.pose[rid]
            dx, dy = where[0] - x, where[1] - y
            dist = math.hypot(dx, dy)
            if dist < 0.05:
                plant.set_velocity(rid, 0.0, 0.0)
                self._next(sim_time)
                return
            err = amr.wrap_angle(math.atan2(dy, dx) - yaw)
            v = 0.0 if abs(err) > math.radians(50) else min(AMR_SPEED, dist)
            plant.set_velocity(rid, v, max(-amr.MAX_ANGULAR, min(amr.MAX_ANGULAR, 2.0 * err)))
            return
        if not self.busy:
            plant.load(data, rid, {"command_id": f"{rid}-{self.index}", "manipulation": kind,
                                   "object_id": self.layout.pallet_id,
                                   "target_pose": {"position": list(self.layout.place_pose(where))}})
            self.busy = True
        for r, result in results:
            if r == rid and result["command_id"] == f"{rid}-{self.index}":
                self.busy = False
                self._next(sim_time)

    def _next(self, sim_time):
        self.index = (self.index + 1) % len(self.steps)
        self.wait_until = sim_time + 0.5


class PlantMotion:
    def __init__(self, model, data, kins=None, plant_amrs: amr.PlantAmrs | None = None,
                 arm_layout=arms.PLANT_ARMS, amr_layout=amr.PLANT_AMRS):
        self.model = model
        self.arms = [ArmReplay(model, data, kins[a.robot_id], a, offset=1.7 * i)
                     for i, a in enumerate(arm_layout) if kins and a.robot_id in kins]
        self.plant_amrs = plant_amrs
        self.amrs = [AmrScript(a, AMR_START_S[i % len(AMR_START_S)]) for i, a in enumerate(amr_layout)] \
            if plant_amrs else []

    def step(self, data, sim_time, dt):
        for arm in self.arms:
            arm.apply(self.model, data, sim_time)
        if self.plant_amrs:
            self.plant_amrs.step(data, dt)
            results = self.plant_amrs.pop_results()
            for script in self.amrs:
                script.step(self.plant_amrs, data, sim_time, results)
