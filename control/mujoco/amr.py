"""이동형 운반 로봇(AMR): 팔레트를 싣고 창고 랙 구역과 TIAGo 작업 셀 사이를 오간다.

- 형상: 낮은 사각 차체(롤러 상판) 위에 팔레트를 싣는다. 팔레트는 차체보다 커서 실리면 차체 밖으로 나온다.
- 팔레트 거치대(스탠드)는 상판과 같은 높이다. AMR이 거치대 앞에 서면 제자리에서 거치대 쪽으로
  방향을 맞춘 뒤 팔레트를 앞쪽에서 끌어 싣거나(PICK) 앞쪽으로 내려놓는다(PLACE). 물리 접촉 대신 운동학적으로 붙인다(실린 팔레트는 차체를 따라간다).
- 차체 이동도 운동학(유니사이클)이다. 바퀴 마찰·미끄러짐은 다루지 않는다.

``plant_scene.xml``은 바꾸지 않고 MjSpec으로 더한다(``add_plant_amrs``, ``arms.build_plant_with_arms``의 extras).
브리지(``mujoco_bridge``의 ``amrs:=true``)는 ``PlantAmrs``로 차체를 움직이고 싣기·내리기를 처리한다.

    python mujoco/amr.py --snapshot outputs/amr   # 팔레트를 실은 AMR 2대가 통로를 지나는 장면 저장
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

ROBOT_TYPE = "MOBILE"  # 스키마 v1.1 robot_type
ROBOT_MODEL = "amr"

CHASSIS_HALF = (0.45, 0.33)  # 차체 반길이 (x 앞뒤, y 좌우)
DECK_TOP = 0.30  # 롤러 상판 높이 = 거치대 높이
PALLET_HALF = (0.55, 0.42)  # 1.1 x 0.84 m 팔레트. 차체보다 커서 실리면 앞뒤·좌우로 나온다
PALLET_HEIGHT = 0.14
CARGO_HEIGHT = 0.60  # 팔레트 위 상자 더미
LOAD_OFFSET_M = CHASSIS_HALF[0] + PALLET_HALF[0] + 0.02  # 차체 중심 → 앞 거치대 중심
LOAD_REACH_M = 0.15  # 싣기·내리기 위치와 차체 중심 거리가 LOAD_OFFSET_M에서 이만큼 안이어야 한다
TRANSFER_S = 2.0  # 싣기·내리기 한 번 (롤러 이송)
MAX_SPEED = 0.5  # 운동학 차체 속도 상한 (m/s). 어댑터가 SR-01 상한 이하로 다시 제한한다
MAX_ANGULAR = 0.8  # rad/s


def footprint_radius(loaded: bool) -> float:
    """사전 시뮬레이션 원기둥 반경. 실린 팔레트까지 덮는다."""
    hx, hy = (max(CHASSIS_HALF[0], PALLET_HALF[0]), max(CHASSIS_HALF[1], PALLET_HALF[1])) if loaded \
        else CHASSIS_HALF
    return math.hypot(hx, hy)


def footprint_height(loaded: bool) -> float:
    return DECK_TOP + (PALLET_HEIGHT + CARGO_HEIGHT if loaded else 0.0)


@dataclass(frozen=True)
class PlantAmr:
    robot_id: str
    pallet_id: str
    warehouse_dock: tuple[float, float, float]  # 창고 쪽 정차 위치 (x, y, yaw: 거치대를 바라봄)
    cell_dock: tuple[float, float, float]  # TIAGo 작업 셀 쪽 정차 위치

    @staticmethod
    def stand_of(dock):
        x, y, yaw = dock
        return (x + LOAD_OFFSET_M * math.cos(yaw), y + LOAD_OFFSET_M * math.sin(yaw))

    @property
    def warehouse_stand(self):
        return self.stand_of(self.warehouse_dock)

    @property
    def cell_stand(self):
        return self.stand_of(self.cell_dock)

    def place_pose(self, stand_xy):
        """PICK·PLACE target_pose.position: 거치대 위 팔레트 바닥 중심."""
        return (stand_xy[0], stand_xy[1], DECK_TOP)


# ---- 공장 현장(plant_scene.xml) 배치 ----
# 창고 랙 구역(arms.PLANT_ARMS, x≈3.5~5.6)의 서쪽 끝 거치대 ↔ TIAGo 작업 셀(테이블 A·B 바깥쪽) 거치대.
# 두 통로 y=±1.75는 작업자 1의 경로점 (3.4, ±1.8)을 지나므로 AMR이 작업자 통로를 가로지른다.
# 북쪽 통로는 UR10e 팔레트(y≥2.25)와 iiwa14 팔레트(y≤1.15) 사이다.
PLANT_AMRS = [
    PlantAmr("amr-01", "pallet:amr-01", (3.7, 1.75, 0.0), (2.05, 1.75, math.pi)),
    PlantAmr("amr-02", "pallet:amr-02", (3.7, -1.75, 0.0), (2.05, -1.75, math.pi)),
]

CHASSIS_RGBA = [0.95, 0.75, 0.1, 1]  # 안전 노랑
PALLET_RGBA = [0.62, 0.46, 0.26, 1]
CARGO_RGBA = [0.78, 0.6, 0.38, 1]
STAND_RGBA = [0.3, 0.32, 0.35, 1]


def _add_pallet(world, name, pos):
    """팔레트 + 상자 더미. 바디 원점은 팔레트 바닥 중심이다."""
    box = mujoco.mjtGeom.mjGEOM_BOX
    body = world.add_body(name=name, pos=list(pos), mocap=True)
    hx, hy = PALLET_HALF
    # 상판·하판과 받침목 3줄 (지게차 포크 구멍이 보이게)
    body.add_geom(name=f"{name}/deck", type=box, size=[hx, hy, 0.02], pos=[0, 0, PALLET_HEIGHT - 0.02],
                  rgba=PALLET_RGBA, contype=0, conaffinity=0)
    body.add_geom(name=f"{name}/bottom", type=box, size=[hx, hy, 0.015], pos=[0, 0, 0.015],
                  rgba=PALLET_RGBA, contype=0, conaffinity=0)
    for i, y in enumerate((-hy + 0.06, 0.0, hy - 0.06)):
        body.add_geom(name=f"{name}/block_{i}", type=box, size=[hx, 0.05, PALLET_HEIGHT / 2 - 0.035],
                      pos=[0, y, PALLET_HEIGHT / 2], rgba=[0.5, 0.36, 0.2, 1], contype=0, conaffinity=0)
    # 상자 더미: 아래 4개, 위 2개, 랩 감은 느낌의 약간 다른 색
    half = 0.15
    for i, (x, y, z) in enumerate([(-0.27, -0.2, 0), (0.27, -0.2, 0), (-0.27, 0.2, 0), (0.27, 0.2, 0),
                                   (-0.15, 0.0, 1), (0.2, 0.0, 1)]):
        shade = 0.92 + 0.08 * ((i * 37) % 3) / 2
        body.add_geom(name=f"{name}/cargo_{i}", type=box, size=[0.25, 0.19, half],
                      pos=[x, y, PALLET_HEIGHT + half + z * 2 * half],
                      rgba=[CARGO_RGBA[0] * shade, CARGO_RGBA[1] * shade, CARGO_RGBA[2] * shade, 1],
                      contype=0, conaffinity=0)
    return body


def _add_stand(world, name, xy):
    """거치대: 상판 높이와 같은 롤러 스탠드. 다리 4개 + 롤러 판."""
    box = mujoco.mjtGeom.mjGEOM_BOX
    hx, hy = PALLET_HALF[0] - 0.05, PALLET_HALF[1] - 0.04
    world.add_geom(name=f"{name}/top", type=box, pos=[xy[0], xy[1], DECK_TOP - 0.02], size=[hx, hy, 0.02],
                   rgba=[0.55, 0.57, 0.6, 1])
    for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
        world.add_geom(name=f"{name}/leg_{i}", type=box, size=[0.03, 0.03, (DECK_TOP - 0.04) / 2],
                       pos=[xy[0] + sx * (hx - 0.03), xy[1] + sy * (hy - 0.03), (DECK_TOP - 0.04) / 2],
                       rgba=STAND_RGBA)


def _add_amr(world, robot_id, dock):
    """AMR 차체(mocap). 원점은 바닥 위 차체 중심이고 +x가 앞(거치대 쪽)이다."""
    box, cyl = mujoco.mjtGeom.mjGEOM_BOX, mujoco.mjtGeom.mjGEOM_CYLINDER
    x, y, yaw = dock
    body = world.add_body(name=f"amr:{robot_id}", pos=[x, y, 0], mocap=True,
                          quat=[math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)])
    hx, hy = CHASSIS_HALF
    clearance = 0.04
    body.add_geom(name=f"amr:{robot_id}/chassis", type=box, size=[hx, hy, (DECK_TOP - 0.03 - clearance) / 2],
                  pos=[0, 0, clearance + (DECK_TOP - 0.03 - clearance) / 2], rgba=CHASSIS_RGBA,
                  contype=0, conaffinity=0)
    body.add_geom(name=f"amr:{robot_id}/rollers", type=box, size=[hx - 0.02, hy - 0.02, 0.015],
                  pos=[0, 0, DECK_TOP - 0.015], rgba=[0.2, 0.2, 0.22, 1], contype=0, conaffinity=0)
    # 범퍼(검정)와 앞쪽 상태등(청록), 바퀴
    for i, sx in enumerate((-1, 1)):
        body.add_geom(name=f"amr:{robot_id}/bumper_{i}", type=box, size=[0.02, hy, 0.04],
                      pos=[sx * (hx + 0.02), 0, 0.12], rgba=[0.08, 0.08, 0.08, 1], contype=0, conaffinity=0)
    body.add_geom(name=f"amr:{robot_id}/light", type=box, size=[0.01, hy * 0.7, 0.012],
                  pos=[hx + 0.041, 0, 0.2], rgba=[0.1, 0.9, 0.85, 1], contype=0, conaffinity=0)
    for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
        body.add_geom(name=f"amr:{robot_id}/wheel_{i}", type=cyl, size=[0.05, 0.02],
                      pos=[sx * (hx - 0.1), sy * (hy + 0.005), 0.05], euler=[math.pi / 2, 0, 0],
                      rgba=[0.1, 0.1, 0.1, 1], contype=0, conaffinity=0)
    return body


def add_plant_amrs(spec: mujoco.MjSpec, amrs=PLANT_AMRS):
    """장면 spec에 AMR·거치대·팔레트를 더한다. 팔레트는 창고 거치대에서, AMR은 창고 정차 위치에서 시작한다."""
    world = spec.worldbody
    for amr in amrs:
        _add_stand(world, f"stand:{amr.robot_id}:warehouse", amr.warehouse_stand)
        _add_stand(world, f"stand:{amr.robot_id}:cell", amr.cell_stand)
        _add_pallet(world, amr.pallet_id, amr.place_pose(amr.warehouse_stand))
        _add_amr(world, amr.robot_id, amr.warehouse_dock)


def wrap_angle(a):
    return math.atan2(math.sin(a), math.cos(a))


class PlantAmrs:
    """브리지 장면 안의 AMR들. rclpy 없이 시험할 수 있다.

    - ``set_velocity``: 어댑터의 /cmd_vel (선속도, 각속도). ``cmd_timeout`` 동안 새 명령이 없으면 정지.
    - ``load``: 싣기·내리기 명령(JSON dict). 조건이 맞으면 롤러 이송을 시작하고, 끝나면 ``results``에 결과를 쌓는다.
    - ``step(dt)``: 차체·팔레트를 움직인다.
    """

    def __init__(self, model, data, amrs=PLANT_AMRS, cmd_timeout=0.5):
        self.model = model
        self.layout = {a.robot_id: a for a in amrs}
        self.mocap = {a.robot_id: model.body_mocapid[model.body(f"amr:{a.robot_id}").id] for a in amrs}
        self.pallets = {model.body(i).name: model.body_mocapid[i] for i in range(model.nbody)
                        if model.body(i).name.startswith("pallet:") and model.body_mocapid[i] >= 0}
        self.pose = {}
        for rid, m in self.mocap.items():
            q = data.mocap_quat[m]
            self.pose[rid] = [float(data.mocap_pos[m][0]), float(data.mocap_pos[m][1]),
                              2 * math.atan2(float(q[3]), float(q[0]))]
        self.twist = {rid: (0.0, 0.0) for rid in self.mocap}
        self.cmd_age = {rid: math.inf for rid in self.mocap}
        self.cmd_timeout = cmd_timeout
        self.held: dict[str, str] = {}  # robot_id → pallet 이름
        self.transfer: dict[str, dict] = {}  # 진행 중인 싣기·내리기
        self.results: list[tuple[str, dict]] = []  # (robot_id, 결과)

    # ---- 입력 ----
    def set_velocity(self, robot_id, linear, angular):
        if robot_id in self.transfer:
            linear, angular = 0.0, 0.0  # 이송 중에는 차체를 세운다
        self.twist[robot_id] = (max(-MAX_SPEED, min(MAX_SPEED, float(linear))),
                                max(-MAX_ANGULAR, min(MAX_ANGULAR, float(angular))))
        self.cmd_age[robot_id] = 0.0

    def load(self, data, robot_id, cmd: dict):
        """싣기·내리기 명령. 바로 거절하면 결과를 results에 넣는다."""
        cid = cmd.get("command_id")
        kind = cmd.get("manipulation")
        if kind == "CANCEL":
            tr = self.transfer.pop(robot_id, None)
            if tr:
                # 이송을 멈춘 자리에 둔다. 차체 위에 반쯤 걸쳤으면 들고 있는 것으로 보지 않는다
                self.held.pop(robot_id, None)
                self.results.append((robot_id, self._result(cid, False, "CANCELLED", "이송 취소")))
            return
        if robot_id in self.transfer:
            self.results.append((robot_id, self._result(cid, False, "ROBOT_BUSY", "이송 중")))
            return
        position = (cmd.get("target_pose") or {}).get("position")
        object_id = cmd.get("object_id")
        center = self.pose[robot_id][:2]
        if kind == "PICK":
            if robot_id in self.held:
                self.results.append((robot_id, self._result(cid, False, "INVALID", "이미 팔레트를 싣고 있음")))
                return
            if object_id not in self.pallets or object_id in self.held.values():
                self.results.append((robot_id, self._result(cid, False, "INVALID", f"{object_id} 없음")))
                return
            start = data.mocap_pos[self.pallets[object_id]].copy()
            off = abs(math.dist(start[:2], center) - LOAD_OFFSET_M)
            if off > LOAD_REACH_M:
                self.results.append((robot_id, self._result(
                    cid, False, "INVALID", f"{object_id}이(가) 차체 앞 거치대 자리에 없음 ({off:.2f} m)")))
                return
            end = None
            goal_xy = start[:2]  # 차체 위 (매 스텝 차체 위치로)
        elif kind == "PLACE":
            if self.held.get(robot_id) != object_id:
                self.results.append((robot_id, self._result(cid, False, "INVALID", f"{object_id}을(를) 싣고 있지 않음")))
                return
            if not (isinstance(position, (list, tuple)) and len(position) == 3):
                self.results.append((robot_id, self._result(cid, False, "INVALID", "target_pose.position 필요")))
                return
            end = np.array([float(v) for v in position])
            off = abs(math.dist(end[:2], center) - LOAD_OFFSET_M)
            if off > LOAD_REACH_M:
                self.results.append((robot_id, self._result(
                    cid, False, "INVALID", f"내릴 위치가 차체 앞 거치대 자리가 아님 ({off:.2f} m)")))
                return
            start = None
            goal_xy = end[:2]
        else:
            self.results.append((robot_id, self._result(cid, False, "UNSUPPORTED", f"{kind!r}")))
            return
        self.twist[robot_id] = (0.0, 0.0)
        yaw = math.atan2(goal_xy[1] - center[1], goal_xy[0] - center[0])
        self.transfer[robot_id] = {"cid": cid, "kind": kind, "object_id": object_id, "t": 0.0,
                                   "start": start, "end": end, "yaw": yaw}

    # ---- 주기 ----
    def step(self, data, dt):
        for rid, m in self.mocap.items():
            self.cmd_age[rid] += dt
            if self.cmd_age[rid] > self.cmd_timeout:
                self.twist[rid] = (0.0, 0.0)
            v, w = self.twist[rid]
            x, y, yaw = self.pose[rid]
            x += v * math.cos(yaw) * dt
            y += v * math.sin(yaw) * dt
            yaw = wrap_angle(yaw + w * dt)
            self.pose[rid] = [x, y, yaw]
            data.mocap_pos[m] = [x, y, 0.0]
            data.mocap_quat[m] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
            self._step_transfer(data, rid, dt)
            if rid in self.held and rid not in self.transfer:
                self._carry(data, rid)

    def _step_transfer(self, data, rid, dt):
        tr = self.transfer.get(rid)
        if not tr:
            return
        err = wrap_angle(tr["yaw"] - self.pose[rid][2])
        if abs(err) > 1e-3:
            # 먼저 제자리에서 거치대 쪽으로 돈다 (실린 팔레트는 차체를 따라 돈다)
            turn = max(-MAX_ANGULAR * dt, min(MAX_ANGULAR * dt, err))
            self.pose[rid][2] = wrap_angle(self.pose[rid][2] + turn)
            yaw = self.pose[rid][2]
            data.mocap_quat[self.mocap[rid]] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
            if rid in self.held:
                self._carry(data, rid)
            return
        tr["t"] += dt
        a = min(tr["t"] / TRANSFER_S, 1.0)
        s = (1 - math.cos(math.pi * a)) / 2
        deck = np.array([*self.pose[rid][:2], DECK_TOP])
        start = tr["start"] if tr["start"] is not None else deck
        end = tr["end"] if tr["end"] is not None else deck
        pallet = self.pallets[tr["object_id"]]
        data.mocap_pos[pallet] = start + s * (end - start)
        yaw = self.pose[rid][2]
        data.mocap_quat[pallet] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
        if a < 1.0:
            return
        del self.transfer[rid]
        if tr["kind"] == "PICK":
            self.held[rid] = tr["object_id"]
        else:
            self.held.pop(rid, None)
        done = "싣기" if tr["kind"] == "PICK" else "내리기"
        self.results.append((rid, self._result(tr["cid"], True, None, f"{tr['kind']} ({done}) 완료")))

    def _carry(self, data, rid):
        x, y, yaw = self.pose[rid]
        pallet = self.pallets[self.held[rid]]
        data.mocap_pos[pallet] = [x, y, DECK_TOP]
        data.mocap_quat[pallet] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]

    @staticmethod
    def _result(cid, success, reason, message):
        return {"command_id": cid, "success": success, "reason_code": reason, "message": message}

    def odom(self, robot_id):
        """(x, y, yaw, 선속도, 각속도)"""
        x, y, yaw = self.pose[robot_id]
        v, w = self.twist[robot_id]
        return x, y, yaw, v, w

    def pop_results(self):
        out, self.results = self.results, []
        return out


def build_plant_with_amrs(with_arms=True):
    """plant_scene.xml + (팔 4대) + AMR 2대. (model, data, {robot_id: ArmKinematics}, PlantAmrs)."""
    import arms
    model, data, kins = arms.build_plant_with_arms(arms.PLANT_ARMS if with_arms else [],
                                                   extras=(add_plant_amrs,))
    return model, data, kins, PlantAmrs(model, data)


def drive_to(plant: PlantAmrs, model, data, robot_id, goal, speed=0.4, dt=0.02, limit_s=60.0):
    """시험·스냅샷용 간단한 추종기: 제자리 회전 후 직진 (어댑터 제어기와 같은 규칙)."""
    t = 0.0
    while t < limit_s:
        x, y, yaw = plant.pose[robot_id]
        dx, dy = goal[0] - x, goal[1] - y
        dist = math.hypot(dx, dy)
        if dist < 0.03:
            plant.set_velocity(robot_id, 0, 0)
            return True
        err = wrap_angle(math.atan2(dy, dx) - yaw)
        w = max(-MAX_ANGULAR, min(MAX_ANGULAR, 2.0 * err))
        v = 0.0 if abs(err) > math.radians(50) else min(speed, dist)
        plant.set_velocity(robot_id, v, w)
        plant.step(data, dt)
        t += dt
    return False


def turn_to(plant: PlantAmrs, data, robot_id, yaw_goal, dt=0.02):
    for _ in range(1000):
        err = wrap_angle(yaw_goal - plant.pose[robot_id][2])
        if abs(err) < 0.01:
            break
        plant.set_velocity(robot_id, 0, max(-MAX_ANGULAR, min(MAX_ANGULAR, 2.0 * err)))
        plant.step(data, dt)
    plant.set_velocity(robot_id, 0, 0)


def run_demo(snapshot_dir: Path | None = None):
    """AMR 2대가 창고 팔레트를 싣고 작업 셀 쪽으로 가는 도중의 장면. {robot_id: 싣기 성공}."""
    model, data, _, plant = build_plant_with_amrs()
    ok = {}
    for amr in PLANT_AMRS:
        plant.load(data, amr.robot_id, {"command_id": "c", "manipulation": "PICK", "object_id": amr.pallet_id})
        for _ in range(int(TRANSFER_S / 0.02) + 2):
            plant.step(data, 0.02)
        ok[amr.robot_id] = plant.held.get(amr.robot_id) == amr.pallet_id
    # 통로 중간(작업자 1 경로점 근처)까지 몰고 간다
    for amr in PLANT_AMRS:
        mid = ((amr.warehouse_dock[0] + amr.cell_dock[0]) / 2 + 0.2, amr.warehouse_dock[1])
        drive_to(plant, model, data, amr.robot_id, mid)
    mujoco.mj_forward(model, data)
    if snapshot_dir:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        renderer = mujoco.Renderer(model, 480, 640)
        views = {name: name for name in ("cctv_1", "cctv_2")}
        close = mujoco.MjvCamera()
        close.lookat[:] = [2.9, 0.0, 0.3]
        close.distance, close.azimuth, close.elevation = 7.0, 90, -42
        views["amr_close"] = close
        for name, cam in views.items():
            renderer.update_scene(data, cam)
            Image.fromarray(renderer.render()).save(snapshot_dir / f"{name}.png")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", type=Path, help="CCTV 2대와 근접 시점 이미지를 저장할 폴더")
    args = parser.parse_args()
    for rid, ok in run_demo(args.snapshot).items():
        print(f"{rid}: 팔레트 싣기 {'성공' if ok else '실패'}")


if __name__ == "__main__":
    main()
