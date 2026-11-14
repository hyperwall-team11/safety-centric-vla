"""여러 로봇 팔을 같은 방식으로 다루는 공통 계층 (이슈 #95).

로봇마다 다른 것은 ``ARM_MODELS`` 한 줄(모델 파일, 관절 이름, 도달 거리)뿐이고, 나머지는 모두 공통이다.

- 장면 구성: MuJoCo Menagerie 모델을 MjSpec으로 불러와 공통 평행 그리퍼(``tool``)를 끝단에 붙이고,
  ``attach_arm``으로 원하는 위치·방향에 여러 대를 배치한다.
- ``ArmKinematics``: 관절 수(6축·7축)와 무관한 감쇠 최소제곱 역기구학. 그리퍼가 아래를 향하는 자세만 푼다.
- ``plan_manipulation``: 제어 계약 4.3절 MANIPULATE(``PICK``·``PLACE``)를 관절 궤적으로 바꾼다.
  사전 시뮬레이션(twin_simulate.py)과 ROS 2 팔 어댑터(robot_adapter.arm_adapter_node)가 함께 쓴다.

파지는 물리 접촉 대신 운동학적으로 붙인다(집으면 물체가 TCP를 따라간다). 사전 시뮬레이션은 궤적의 충돌·거리만 보고,
어댑터도 시뮬레이션 백엔드이므로 그리퍼 마찰·미끄러짐은 다루지 않는다.

모델은 저장소에 없다. ``pixi run fetch-models``로 받는다.

    python mujoco/arms.py --snapshot outputs/arms   # 공장 현장에 4대를 두고 각자 집기·놓기, CCTV 시점 저장
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

MODELS_DIR = Path(__file__).resolve().parent / "models"
ROBOT_TYPE = "MANIPULATOR"


@dataclass(frozen=True)
class ArmModel:
    directory: str  # MuJoCo Menagerie 폴더 (scripts/fetch_models.sh)
    xml: str
    joints: tuple[str, ...]
    reach_m: float  # 제조사 공칭 도달 거리
    label: str
    tool: str = "parallel"  # TOOLS 키

    @property
    def path(self) -> Path:
        return MODELS_DIR / self.directory / self.xml


UR_JOINTS = ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
             "wrist_1_joint", "wrist_2_joint", "wrist_3_joint")
SEVEN_JOINTS = tuple(f"joint{i}" for i in range(1, 8))

# 새 로봇은 여기 한 줄을 추가한다. 끝단에 attachment_site가 있고 관절이 모두 hinge인 모델이면 된다.
ARM_MODELS = {
    "ur5e": ArmModel("universal_robots_ur5e", "ur5e.xml", UR_JOINTS, 0.85, "Universal Robots UR5e"),
    "ur10e": ArmModel("universal_robots_ur10e", "ur10e.xml", UR_JOINTS, 1.30, "Universal Robots UR10e",
                      tool="clamp"),
    "iiwa14": ArmModel("kuka_iiwa_14", "iiwa14.xml", SEVEN_JOINTS, 0.82, "KUKA LBR iiwa 14", tool="clamp"),
    "panda": ArmModel("franka_emika_panda", "panda_nohand.xml", SEVEN_JOINTS, 0.855, "Franka Emika Panda"),
}



@dataclass(frozen=True)
class Tool:
    """끝단 공구. attachment_site 기준 z축(플랜지 바깥 방향)으로 손바닥 → 손가락. TCP는 잡은 상자의 중심."""
    palm_half: tuple[float, float, float]
    finger_half: tuple[float, float, float]
    finger_x: float  # 손가락 중심의 좌우 위치
    tcp_depth: float  # 플랜지에서 TCP까지
    approach_height: float  # 집기·놓기 전후로 상자 중심 위에서 멈추는 높이 (손가락이 상자 위로 빠지는 높이)
    max_box: float  # 잡을 수 있는 상자 폭 (손가락 안쪽 간격보다 작게)


TOOLS = {
    # 협동로봇용 평행 그리퍼: 0.10 m 부품 상자
    "parallel": Tool((0.085, 0.02, 0.015), (0.008, 0.02, 0.045), 0.075, 0.105, 0.15, 0.12),
    # 적재용 박스 클램프: 0.30 m 상자를 양옆에서 잡는다
    "clamp": Tool((0.20, 0.07, 0.02), (0.012, 0.07, 0.13), 0.185, 0.17, 0.40, 0.33),
}
GOAL_TOLERANCE_M = 0.02
MIN_SEGMENT_S = 0.3
GRIP_DWELL_S = 0.5


class ArmModelMissing(FileNotFoundError):
    pass


def arm_model(key: str) -> ArmModel:
    if key not in ARM_MODELS:
        raise KeyError(f"unknown robot_model {key!r} (supported: {', '.join(sorted(ARM_MODELS))})")
    spec = ARM_MODELS[key]
    if not spec.path.is_file():
        raise ArmModelMissing(f"{spec.path} 없음: pixi run fetch-models")
    return spec


def _add_tool(child: mujoco.MjSpec, t: Tool) -> None:
    site = child.site("attachment_site")
    tool = site.parent.add_body(name="tool", pos=site.pos, quat=site.quat)
    box = mujoco.mjtGeom.mjGEOM_BOX
    tool.add_geom(name="palm", type=box, size=t.palm_half, pos=[0, 0, t.palm_half[2]],
                  rgba=[0.25, 0.25, 0.25, 1])
    finger_z = 2 * t.palm_half[2] + t.finger_half[2]
    for side, x in (("l", -t.finger_x), ("r", t.finger_x)):
        tool.add_geom(name=f"finger_{side}", type=box, size=t.finger_half, pos=[x, 0, finger_z],
                      rgba=[0.35, 0.35, 0.35, 1])
    tool.add_site(name="tcp", pos=[0, 0, t.tcp_depth], size=[0.01, 0, 0], rgba=[1, 0, 0, 1])


def load_arm_spec(key: str) -> tuple[mujoco.MjSpec, np.ndarray]:
    """Menagerie 모델에 공통 그리퍼를 붙인 MjSpec과 모델 파일의 home 관절각."""
    model = arm_model(key)
    child = mujoco.MjSpec.from_file(str(model.path))
    home = None
    for k in list(child.keys):
        if k.name == "home" and len(k.qpos) >= len(model.joints):
            home = np.array(list(k.qpos), dtype=float)[:len(model.joints)]
        child.delete(k)  # 합친 장면의 관절 수와 맞지 않으므로 keyframe은 버리고 home만 따로 보관
    _add_tool(child, TOOLS[model.tool])
    return child, (home if home is not None else np.zeros(len(model.joints)))


def attach_arm(parent: mujoco.MjSpec, key: str, prefix: str, position, yaw: float = 0.0) -> np.ndarray:
    """parent 장면의 position(바닥 위 베이스)에 yaw만큼 돌려 팔을 붙인다. 이름은 모두 prefix로 시작한다."""
    child, home = load_arm_spec(key)
    frame = parent.worldbody.add_frame(pos=[float(v) for v in position],
                                       quat=[math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)])
    # 적분기 등 option은 부모 장면 값을 쓴다. 미리 맞춰 두면 attach 충돌 경고가 나지 않는다
    child.option.integrator = parent.option.integrator
    child.option.impratio = parent.option.impratio
    parent.attach(child, prefix=prefix, frame=frame)
    return home


class ArmKinematics:
    """합쳐진 장면 안의 팔 한 대. prefix 아래의 관절만 움직인다."""

    def __init__(self, model: mujoco.MjModel, key: str, prefix: str, home=None):
        self.model, self.key, self.prefix = model, key, prefix
        spec = arm_model(key)
        self.joint_names = list(spec.joints)
        joint_ids = [model.joint(prefix + j).id for j in spec.joints]
        self.qpos_ids = np.array([model.jnt_qposadr[j] for j in joint_ids])
        self.dof_ids = np.array([model.jnt_dofadr[j] for j in joint_ids])
        self.ranges = model.jnt_range[joint_ids].copy()
        self.limited = model.jnt_limited[joint_ids].astype(bool)
        self.tcp_id = model.site(prefix + "tcp").id
        self.base_body = model.body(prefix + "base").id if _has_body(model, prefix + "base") else None
        self.home = np.asarray(home if home is not None else np.zeros(len(joint_ids)), dtype=float)
        self.reach = spec.reach_m
        self.tool = TOOLS[spec.tool]
        self.body_ids = {i for i in range(model.nbody) if model.body(i).name.startswith(prefix)}
        self.geom_ids = [g for g in range(model.ngeom) if model.geom_bodyid[g] in self.body_ids
                         and (model.geom_contype[g] or model.geom_conaffinity[g])]
        # 관절마다 그 관절을 움직이는 위치 액추에이터 (Menagerie 모델은 관절당 하나). 운동학 전용 장면이면 없음
        by_joint = {int(model.actuator_trnid[a, 0]): a for a in range(model.nu)
                    if model.actuator_trntype[a] == mujoco.mjtTrn.mjTRN_JOINT}
        self.actuator_ids = np.array([by_joint.get(j, -1) for j in joint_ids])
        self._scratch = mujoco.MjData(model)

    def command(self, data, q):
        """위치 액추에이터 목표를 q로 둔다(물리 스텝용). 액추에이터가 없는 관절은 건너뛴다."""
        ok = self.actuator_ids >= 0
        data.ctrl[self.actuator_ids[ok]] = np.asarray(q, dtype=float)[ok]

    @property
    def dof(self) -> int:
        return len(self.qpos_ids)

    def clip(self, q):
        q = np.array(q, dtype=float)
        lo, hi = self.ranges[:, 0], self.ranges[:, 1]
        return np.where(self.limited, np.clip(q, lo, hi), q)

    def set(self, data, q):
        data.qpos[self.qpos_ids] = q

    def get(self, data):
        return data.qpos[self.qpos_ids].copy()

    def tcp(self, data):
        return data.site_xpos[self.tcp_id].copy()

    def base_xy(self, data):
        if self.base_body is None:
            return None
        return data.xpos[self.base_body][:2].copy()

    def solve_ik(self, data, target, q0, iterations=150, damping=1e-3, tol=1e-4):
        """TCP를 target으로 보내고 그리퍼 z축을 아래(-z)로 맞춘다. (관절각, 위치 오차 m)."""
        scratch = self._scratch
        scratch.qpos[:] = data.qpos
        scratch.mocap_pos[:] = data.mocap_pos
        q = self.clip(q0)
        target = np.asarray(target, dtype=float)
        jacp, jacr = np.zeros((3, self.model.nv)), np.zeros((3, self.model.nv))
        down = np.array([0.0, 0.0, -1.0])
        pos_err = np.inf
        for _ in range(iterations):
            scratch.qpos[self.qpos_ids] = q
            mujoco.mj_kinematics(self.model, scratch)
            mujoco.mj_comPos(self.model, scratch)
            tcp = scratch.site_xpos[self.tcp_id]
            z_axis = scratch.site_xmat[self.tcp_id].reshape(3, 3)[:, 2]
            err = np.concatenate([target - tcp, np.cross(z_axis, down)])
            pos_err = float(np.linalg.norm(err[:3]))
            if np.linalg.norm(err) < tol:
                break
            mujoco.mj_jacSite(self.model, scratch, jacp, jacr, self.tcp_id)
            jac = np.vstack([jacp, jacr])[:, self.dof_ids]
            dq = jac.T @ np.linalg.solve(jac @ jac.T + damping * np.eye(6), err)
            q = self.clip(q + np.clip(dq, -0.3, 0.3))
        return q, pos_err

    def solve_ik_seeded(self, data, target, q0):
        """국소 최소에 걸리지 않도록 q0·home과, 첫 관절(베이스 회전)을 돌린 자세들에서 풀어 가장 좋은 답을 쓴다."""
        seeds = [self.clip(q0), self.home.copy()]
        for offset in (math.pi / 2, -math.pi / 2, math.pi):
            for base in (q0, self.home):
                seed = np.array(base, dtype=float)
                seed[0] += offset
                seeds.append(self.clip(seed))
        best = None
        for seed in seeds:
            q, err = self.solve_ik(data, target, seed)
            # 같은 오차면 q0에서 덜 움직이는 답
            score = (round(err, 4), float(np.abs(q - q0).sum()))
            if best is None or score < best[0]:
                best = (score, q, err)
            if err < 1e-4 and seed is seeds[0]:
                break
        return best[1], best[2]

    def tcp_at(self, data, q):
        scratch = self._scratch
        scratch.qpos[:] = data.qpos
        scratch.qpos[self.qpos_ids] = q
        mujoco.mj_kinematics(self.model, scratch)
        return scratch.site_xpos[self.tcp_id].copy()


def _has_body(model, name):
    try:
        model.body(name)
        return True
    except KeyError:
        return False


@dataclass
class TrajectoryPoint:
    t: float
    q: np.ndarray
    phase: str  # APPROACH | DESCEND | GRASP | LIFT | RELEASE
    holding: bool  # 이 시점에 물체를 들고 있는가


@dataclass
class ManipulationPlan:
    points: list[TrajectoryPoint]
    reached: bool  # 모든 경유점에서 TCP 오차가 GOAL_TOLERANCE_M 이하
    worst_error_m: float
    reason: str | None = None

    @property
    def duration(self):
        return self.points[-1].t if self.points else 0.0


def plan_manipulation(kin: ArmKinematics, data, q0, manipulation: str, target, speed: float,
                      dt: float = 0.02, holding: bool | None = None) -> ManipulationPlan:
    """MANIPULATE를 관절 궤적으로 바꾼다.

    - PICK: 현재 자세 → 대상 위(APPROACH) → 대상(DESCEND) → 파지 → 다시 위(LIFT)
    - PLACE: 현재 자세 → 놓을 위치 위 → 놓을 위치 → 놓기 → 다시 위
    첫 구간은 관절 공간 보간(현재 자세가 아래를 향하지 않을 수 있으므로), 나머지는 TCP 직선 이동이다.
    speed는 TCP 직선 속도 상한(m/s)이다.
    """
    manipulation = manipulation.upper()
    if manipulation not in ("PICK", "PLACE"):
        raise ValueError(f"manipulation must be PICK or PLACE, got {manipulation!r}")
    if not (math.isfinite(speed) and speed > 0):
        raise ValueError("speed must be finite and > 0")
    target = np.asarray(target, dtype=float)
    above = target + [0, 0, kin.tool.approach_height]
    picking = manipulation == "PICK"
    held_before = (not picking) if holding is None else holding
    q0 = kin.clip(q0)

    q_above, err_above = kin.solve_ik_seeded(data, above, q0)
    q_target, err_target = kin.solve_ik(data, target, q_above)
    worst = max(err_above, err_target)
    reached = worst <= GOAL_TOLERANCE_M

    points = [TrajectoryPoint(0.0, q0.copy(), "APPROACH", held_before)]
    t = 0.0

    def joint_segment(qa, qb, length_m, phase, held):
        nonlocal t
        duration = max(length_m / speed, MIN_SEGMENT_S)
        n = max(1, math.ceil(duration / dt))
        for k in range(1, n + 1):
            a = (1 - math.cos(math.pi * k / n)) / 2
            points.append(TrajectoryPoint(t + duration * k / n, qa + a * (qb - qa), phase, held))
        t += duration

    def line_segment(pa, pb, q_start, phase, held):
        nonlocal t, worst
        length = float(np.linalg.norm(pb - pa))
        duration = max(length / speed, MIN_SEGMENT_S)
        n = max(1, math.ceil(duration / dt))
        q = q_start
        for k in range(1, n + 1):
            q, err = kin.solve_ik(data, pa + (pb - pa) * k / n, q, iterations=30)
            worst = max(worst, err)
            points.append(TrajectoryPoint(t + duration * k / n, q.copy(), phase, held))
        t += duration
        return q

    start_tcp = kin.tcp_at(data, q0)
    joint_segment(q0, q_above, float(np.linalg.norm(above - start_tcp)), "APPROACH", held_before)
    q = line_segment(above, target, q_above, "DESCEND", held_before)
    held_after = picking
    t += GRIP_DWELL_S  # 그리퍼 닫기·열기 (물리 장면에서 관절이 목표에 붙을 시간 포함)
    points.append(TrajectoryPoint(t, q.copy(), "GRASP" if picking else "RELEASE", held_after))
    line_segment(target, above, q, "LIFT", held_after)
    reached = reached and worst <= GOAL_TOLERANCE_M
    reason = None
    if not reached:
        reason = f"target out of reach: TCP error {worst:.3f} m > {GOAL_TOLERANCE_M} m"
    return ManipulationPlan(points, reached, worst, reason)


# ---- 공장 현장(plant_scene.xml) 배치: 창고형 적재·포장 구역 ----
# TIAGo 작업 셀 동쪽 열린 바닥에 둔다. CCTV 2대가 모두 보는 범위이고 작업자 통로와 붙어 있다.
# - 적재 스테이션 A·B: UR10e·iiwa14가 랙 아래 칸의 0.30 m 상자를 바닥 팔레트로 내리고 다시 올린다.
#   팔레트 A는 작업자 1 경로점(3.4, 1.8) 옆, 랙 B 남쪽 끝은 작업자 3 통로 옆이다.
# - 포장대: UR5e·Panda가 0.10 m 부품 상자를 포장대 위에서 옮긴다.
BIG_BOX_HALF = 0.15
SMALL_BOX_HALF = 0.05
PEDESTAL_HALF_XY = 0.18
RACK_HALF = (0.30, 0.70)  # 랙 반길이 (x 깊이, y 길이)
RACK_SHELVES = (0.45, 1.40, 2.00)  # 선반 윗면 높이. 아래 칸 높이 0.95 m는 클램프가 상자 위로 빠질 공간
PALLET_HALF = (0.40, 0.35)
PALLET_TOP = 0.15
PACK_TABLE = ((3.7, 3.8), (0.85, 0.30), 0.60)  # (중심, 반길이, 윗면 높이)


@dataclass(frozen=True)
class PlantArm:
    robot_id: str
    robot_model: str
    base_xy: tuple[float, float]
    pick: tuple[float, float, float]  # 상자 중심. 상자는 이 위치에서 시작한다
    place: tuple[float, float, float]
    pedestal_top: float
    box_half: float


def _station(robot_id, key, y, base_x, pedestal_top):
    """랙(동쪽) ↔ 팔레트(서쪽) 적재 스테이션."""
    pick = (5.25, y, RACK_SHELVES[0] + BIG_BOX_HALF)
    place = (base_x - 0.75, y, PALLET_TOP + BIG_BOX_HALF)
    return PlantArm(robot_id, key, (base_x, y), pick, place, pedestal_top, BIG_BOX_HALF)


def _packer(robot_id, key, x):
    """포장대 북쪽에서 남쪽을 보고, 포장대 위 두 자리 사이로 부품 상자를 옮긴다."""
    (_, ty), _, top = PACK_TABLE
    z = top + SMALL_BOX_HALF
    return PlantArm(robot_id, key, (x, ty + 0.6), (x - 0.22, ty, z), (x + 0.22, ty, z), top, SMALL_BOX_HALF)


PLANT_ARMS = [
    _station("arm-ur10e", "ur10e", 2.6, 4.35, 0.30),
    _station("arm-iiwa14", "iiwa14", 0.8, 4.60, 0.45),  # 팔이 짧아(0.82 m) 받침대를 높여 팔레트까지 닿게 한다
    _packer("arm-ur5e", "ur5e", 3.3),
    _packer("arm-panda", "panda", 4.1),
]


def _base_yaw(arm: PlantArm):
    """베이스가 집기·놓기 위치의 중간 방향을 보게 한다."""
    mid = ((arm.pick[0] + arm.place[0]) / 2, (arm.pick[1] + arm.place[1]) / 2)
    return math.atan2(mid[1] - arm.base_xy[1], mid[0] - arm.base_xy[0])


RACK_POST_RGBA = [0.35, 0.4, 0.5, 1]
RACK_BEAM_RGBA = [0.95, 0.45, 0.1, 1]
CARDBOARD_RGBA = [0.78, 0.6, 0.38, 1]


def _add_rack(world, name, cx, cy, fill_upper=True):
    """창고 랙: 기둥 4개, 선반 3단(오렌지 보). 위 두 칸에는 장식용 상자를 채운다(충돌 없음)."""
    box = mujoco.mjtGeom.mjGEOM_BOX
    hx, hy = RACK_HALF
    top = RACK_SHELVES[-1] + 0.1
    for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1))):
        world.add_geom(name=f"{name}_post_{i}", type=box, pos=[cx + sx * (hx - 0.03), cy + sy * (hy - 0.03), top / 2],
                       size=[0.03, 0.03, top / 2], rgba=RACK_POST_RGBA)
    for k, h in enumerate(RACK_SHELVES):
        world.add_geom(name=f"{name}_shelf_{k}", type=box, pos=[cx, cy, h - 0.015], size=[hx, hy, 0.015],
                       rgba=RACK_BEAM_RGBA)
    if fill_upper:
        for k, h in enumerate(RACK_SHELVES[1:]):
            for j, dy in enumerate((-0.45, 0.0, 0.45)):
                world.add_geom(name=f"{name}_stock_{k}_{j}", type=box, pos=[cx, cy + dy, h + 0.17],
                               size=[0.22, 0.2, 0.17], rgba=CARDBOARD_RGBA, contype=0, conaffinity=0)


def add_plant_arms(spec: mujoco.MjSpec, arms=PLANT_ARMS):
    """장면 spec에 팔·받침대·랙·팔레트·포장대·상자를 더한다. {robot_id: home 관절각}."""
    box = mujoco.mjtGeom.mjGEOM_BOX
    world = spec.worldbody
    homes = {}
    for arm in arms:
        x, y = arm.base_xy
        h = arm.pedestal_top
        world.add_geom(name=f"pedestal:{arm.robot_id}", type=box, pos=[x, y, h / 2],
                       size=[PEDESTAL_HALF_XY, PEDESTAL_HALF_XY, h / 2], rgba=[0.3, 0.32, 0.35, 1])
        homes[arm.robot_id] = attach_arm(spec, arm.robot_model, f"{arm.robot_id}/", (x, y, h), _base_yaw(arm))
        if arm.box_half == BIG_BOX_HALF:
            _add_rack(world, f"rack:{arm.robot_id}", 5.35, y)
            px, py = arm.place[0], arm.place[1]
            world.add_geom(name=f"pallet:{arm.robot_id}", type=box, pos=[px, py, PALLET_TOP / 2],
                           size=[*PALLET_HALF, PALLET_TOP / 2], rgba=[0.6, 0.45, 0.25, 1])
        part = world.add_body(name=f"box:{arm.robot_id}", pos=list(arm.pick), mocap=True)
        part.add_geom(type=box, size=[arm.box_half] * 3, rgba=CARDBOARD_RGBA if arm.box_half == BIG_BOX_HALF
                      else [0.95, 0.6, 0.15, 1], contype=0, conaffinity=0)
    if any(a.box_half == SMALL_BOX_HALF for a in arms):
        (tx, ty), (hx, hy), top = PACK_TABLE
        world.add_geom(name="pack_table", type=box, pos=[tx, ty, top - 0.02], size=[hx, hy, 0.02],
                       rgba=[0.55, 0.4, 0.25, 1])
        for i, sx in enumerate((-1, 1)):
            world.add_geom(name=f"pack_table_leg_{i}", type=box, pos=[tx + sx * (hx - 0.1), ty, (top - 0.04) / 2],
                           size=[0.05, hy - 0.05, (top - 0.04) / 2], rgba=[0.45, 0.33, 0.2, 1])
    return homes


PLANT_SCENE = MODELS_DIR / "plant_scene.xml"


def scene_root(scene_spec: mujoco.MjSpec) -> mujoco.MjSpec:
    """빈 루트 spec에 장면을 붙인다.

    MuJoCo 3.15에서 include·meshdir가 있는 장면 spec에 UR 모델을 직접 attach하면 spec 해제 시 프로세스가
    비정상 종료한다(SIGSEGV·SIGBUS). 빈 루트에 장면과 팔을 차례로 붙이면 이 문제가 없다.
    keyframe은 합친 장면의 관절 수와 맞지 않으므로 지우고, 필요한 자세는 관절 이름으로 다시 넣는다.
    """
    for k in list(scene_spec.keys):
        scene_spec.delete(k)
    root = mujoco.MjSpec()
    root.option.integrator = scene_spec.option.integrator
    root.option.timestep = scene_spec.option.timestep
    root.option.gravity = scene_spec.option.gravity
    root.visual.global_.offwidth = max(scene_spec.visual.global_.offwidth, 640)
    root.visual.global_.offheight = max(scene_spec.visual.global_.offheight, 480)
    root.visual.headlight.ambient = scene_spec.visual.headlight.ambient
    root.visual.headlight.diffuse = scene_spec.visual.headlight.diffuse
    root.attach(scene_spec, prefix="", frame=root.worldbody.add_frame())
    return root


def _keyframe_by_name(path: Path):
    """장면 단독 keyframe 0을 ({관절 이름: qpos 배열}, {액추에이터 이름: ctrl})로 푼다."""
    model = mujoco.MjModel.from_xml_path(str(path))
    if not model.nkey:
        return {}, {}
    qpos = {}
    for j in range(model.njnt):
        adr = model.jnt_qposadr[j]
        size = {0: 7, 1: 4}.get(int(model.jnt_type[j]), 1)
        qpos[model.joint(j).name] = model.key_qpos[0][adr:adr + size].copy()
    ctrl = {model.actuator(a).name: float(model.key_ctrl[0][a]) for a in range(model.nu)}
    return qpos, ctrl


def build_plant_with_arms(arms=PLANT_ARMS):
    """plant_scene.xml에 팔 4대를 더한 (model, data, {robot_id: ArmKinematics})."""
    plant = mujoco.MjSpec.from_file(str(PLANT_SCENE))
    root = scene_root(plant)
    homes = add_plant_arms(root, arms)
    model = root.compile()
    data = mujoco.MjData(model)
    key_qpos, key_ctrl = _keyframe_by_name(PLANT_SCENE)  # TIAGo home 자세·제어값
    for name, values in key_qpos.items():
        adr = model.jnt_qposadr[model.joint(name).id]
        data.qpos[adr:adr + len(values)] = values
    for name, value in key_ctrl.items():
        data.ctrl[model.actuator(name).id] = value
    kins = {}
    for arm in arms:
        kin = ArmKinematics(model, arm.robot_model, f"{arm.robot_id}/", homes[arm.robot_id])
        kin.set(data, kin.home)
        kin.command(data, kin.home)  # 물리 스텝에서 중력에 처지지 않도록 위치 제어 목표도 home
        kins[arm.robot_id] = kin
    mujoco.mj_forward(model, data)
    return model, data, kins


def standalone_arm(key: str, base_position=(0.0, 0.0, 0.0), yaw: float = 0.0):
    """팔 한 대만 있는 운동학 장면 (model, data, ArmKinematics). ROS 2 팔 어댑터가 궤적 계획에 쓴다."""
    root = mujoco.MjSpec()
    home = attach_arm(root, key, "arm/", base_position, yaw)
    model = root.compile()
    data = mujoco.MjData(model)
    kin = ArmKinematics(model, key, "arm/", home)
    kin.set(data, kin.home)
    mujoco.mj_forward(model, data)
    return model, data, kin


class ArmPlanner:
    """어댑터용 궤적 계획기. 팔 한 대의 운동학 장면만 갖고 MANIPULATE를 관절 궤적으로 바꾼다."""

    def __init__(self, key: str, base_position=(0.0, 0.0, 0.0), yaw: float = 0.0):
        self.key = key
        self.base_position = [float(v) for v in base_position]
        self.model, self.data, self.kin = standalone_arm(key, base_position, yaw)

    @property
    def home(self):
        return self.kin.home.copy()

    @property
    def joint_names(self):
        return list(self.kin.joint_names)

    def plan(self, manipulation, target, speed, q_now, holding=None) -> ManipulationPlan:
        return plan_manipulation(self.kin, self.data, np.asarray(q_now, dtype=float), manipulation,
                                 target, speed, holding=holding)

    def tcp(self, q):
        return self.kin.tcp_at(self.data, np.asarray(q, dtype=float))


class PlantArms:
    """브리지 장면 안의 팔들. 어댑터가 보낸 관절 목표·그리퍼 명령을 물리 장면에 반영한다(rclpy 없음).

    그리퍼를 닫으면 TCP에서 ``GRASP_RADIUS_M`` 안의 가장 가까운 상자를 붙이고, 열면 그 자리에 놓는다.
    """

    GRASP_RADIUS_M = 0.05

    def __init__(self, model, data, kins: dict[str, ArmKinematics]):
        self.model, self.kins = model, kins
        self.boxes = {model.body(i).name: model.body_mocapid[i] for i in range(model.nbody)
                      if model.body(i).name.startswith("box:") and model.body_mocapid[i] >= 0}
        self.held: dict[str, int] = {}  # robot_id → mocap id

    def set_target(self, data, robot_id, q):
        kin = self.kins[robot_id]
        if len(q) != kin.dof:
            raise ValueError(f"{robot_id}: {kin.dof} joint targets needed, got {len(q)}")
        kin.command(data, kin.clip(q))

    def set_gripper(self, data, robot_id, closed: bool):
        if not closed:
            self.held.pop(robot_id, None)
            return None
        if robot_id in self.held:
            return self.held[robot_id]
        tcp = self.kins[robot_id].tcp(data)
        taken = set(self.held.values())
        best = min(((float(np.linalg.norm(data.mocap_pos[m] - tcp)), m) for m in self.boxes.values()
                    if m not in taken), default=None)
        if best and best[0] <= self.GRASP_RADIUS_M:
            self.held[robot_id] = best[1]
            return best[1]
        return None

    def after_step(self, data):
        for robot_id, mocap in self.held.items():
            data.mocap_pos[mocap] = self.kins[robot_id].tcp(data)

    def joint_state(self, data, robot_id):
        kin = self.kins[robot_id]
        return kin.get(data), data.qvel[kin.dof_ids].copy()


def run_demo(snapshot_dir: Path | None = None, speed=0.25):
    """4대가 각자 상자를 집어 옮긴다. {robot_id: (계획 성공, 최종 상자 오차 m)}."""
    model, data, kins = build_plant_with_arms()
    results = {}
    for arm in PLANT_ARMS:
        kin = kins[arm.robot_id]
        box = model.body(f"box:{arm.robot_id}").mocapid[0]
        ok = True
        for manipulation, target in (("PICK", arm.pick), ("PLACE", arm.place)):
            plan = plan_manipulation(kin, data, kin.get(data), manipulation, target, speed)
            ok = ok and plan.reached
            for p in plan.points:
                kin.set(data, p.q)
                mujoco.mj_kinematics(model, data)
                if p.holding:
                    data.mocap_pos[box] = kin.tcp(data)
        mujoco.mj_forward(model, data)
        results[arm.robot_id] = (ok, float(np.linalg.norm(data.mocap_pos[box] - arm.place)))
    if snapshot_dir:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        renderer = mujoco.Renderer(model, 480, 640)
        views = {name: name for name in ("cctv_1", "cctv_2")}
        close = mujoco.MjvCamera()
        close.lookat[:] = [4.4, 2.2, 0.6]
        close.distance, close.azimuth, close.elevation = 6.0, 205, -28
        views["arms_close"] = close
        for name, cam in views.items():
            renderer.update_scene(data, cam)
            Image.fromarray(renderer.render()).save(snapshot_dir / f"{name}.png")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", type=Path, help="집기·놓기 후 CCTV 2대와 근접 시점 이미지를 저장할 폴더")
    args = parser.parse_args()
    for rid, (ok, err) in run_demo(args.snapshot).items():
        print(f"{rid:12s} {'성공' if ok and err < 0.03 else '실패'}  상자 오차 {err * 100:.1f} cm")


if __name__ == "__main__":
    main()
