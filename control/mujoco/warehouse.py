"""최종 발표용 창고 배치와 AMR 위치로 시작하는 단발 작업자 횡단."""
import math

import mujoco

from amr import PlantAmr, add_plant_amrs
from scenario import WorkerScript

AMR = PlantAmr("amr-01", "pallet:amr-01", (-4.0, -4.0, 0.0), (4.0, -4.0, 0.0))
WAREHOUSE_AMRS = (
    AMR,
    PlantAmr("amr-02", "pallet:amr-02", (7.55, -8.35, 0.0), (10.35, -8.35, 0.0)),
    PlantAmr("amr-03", "pallet:amr-03", (7.55, -2.75, 0.0), (10.35, -2.75, 0.0)),
)
DEMO_WORKERS = [
    {"waypoints": [(1.0, -1.5, 1.0), (1.0, -6.6, 1.0)], "speed": 0.6,
     "vest": "lime", "pants": "navy", "helmet": "white"},
    {"waypoints": [(-5.7, 4.0, 2.0), (-1.0, 4.0, 1.0)], "speed": 0.55,
     "vest": "orange", "pants": "jeans", "helmet": "yellow"},
    {"waypoints": [(1.2, 5.8, 2.0), (5.8, 5.8, 2.0)], "speed": 0.55,
     "vest": "lime", "pants": "jeans", "helmet": "orange"},
    {"waypoints": [(8.2, 3.0, 1.5), (8.2, 6.5, 2.0)], "speed": 0.5,
     "vest": "orange", "pants": "navy", "helmet": "white"},
]
CROSS_X = 1.0
TRIGGER_X = -2.4
WALK_START_Y, WALK_END_Y = -1.5, -6.6
# 보행자 진입부 감시 영역. 운반 차선 중심과 겹치지 않으며 접근 중 정지 신호를 발행한다.
CROSSING_ENTRY = [(0.4, -3.65), (1.6, -3.65), (1.6, -2.40), (0.4, -2.40)]

STEEL = [0.24, 0.30, 0.35, 1]
ORANGE = [0.92, 0.40, 0.10, 1]
YELLOW = [0.98, 0.76, 0.10, 1]
WHITE = [0.90, 0.91, 0.84, 1]


class CrossingWorker(WorkerScript):
    def __init__(self):
        super().__init__(waypoints=[(CROSS_X, WALK_START_Y, 0.0), (CROSS_X, WALK_END_Y, 1.0)])
        self.started_at = None
        self.duration = abs(WALK_END_Y - WALK_START_Y) / self.speed

    def observe_amr(self, x, speed, sim_time):
        if self.started_at is None and x >= TRIGGER_X and speed > 0:
            self.started_at = sim_time

    def _elapsed(self, sim_time):
        return 0.0 if self.started_at is None else min(sim_time - self.started_at, self.duration)

    def position(self, sim_time):
        return CROSS_X, WALK_START_Y - self._elapsed(sim_time) * self.speed, -math.pi / 2

    def gait_state(self, sim_time):
        elapsed = self._elapsed(sim_time)
        return elapsed * self.speed, float(elapsed == 0.0 or elapsed >= self.duration)



def build_warehouse():
    """공장 장면에서 팔·작업 셀을 떼고 창고에 필요한 모델만 구성한다."""
    from arms import PLANT_SCENE, scene_root

    spec = scene_root(mujoco.MjSpec.from_file(str(PLANT_SCENE)))
    for element in [*spec.actuators, *spec.excludes]:
        spec.delete(element)
    for name in ("base_link", "table_a", "table_b", "part_box"):
        spec.delete(spec.body(name))
    add_warehouse(spec)
    model = spec.compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data

def add_warehouse(spec):
    world = spec.worldbody
    for geom in list(world.geoms):
        if geom.name.startswith("prop_") or geom.name in {"keep_out_zone", "zone_a_marking", "zone_b_marking"}:
            spec.delete(geom)
    for i, worker in enumerate(DEMO_WORKERS[1:], start=2):
        name = f"worker_{i}"
        x, y, _ = worker["waypoints"][0]
        if i == 4:
            world.add_body(name=name, pos=[x, y, 0], mocap=True)
        else:
            spec.body(name).pos = [x, y, 0]

    def box(name, pos, size, color, collidable=True, quat=None):
        world.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=pos, size=size, rgba=color,
                       contype=int(collidable), conaffinity=int(collidable), **({"quat": quat} if quat else {}))

    for index, (x, y) in enumerate(((-3.5, -1.5), (-0.9, -1.5), (3.1, -1.5), (5.8, -1.5),
                                     (-3.3, -7.3), (-0.7, -7.3), (3.1, -7.3), (5.8, -7.3),
                                     (-3.5, 2.5), (-0.9, 2.5), (3.1, 2.5), (5.8, 2.5),
                                     (-3.5, 7.0), (-0.9, 7.0), (3.1, 7.0), (5.8, 7.0))):
        prefix = f"rack_{index}"
        for dx in (-1.08, 1.08):
            for dy in (-0.50, 0.50):
                box(f"{prefix}_post_{dx}_{dy}", [x+dx, y+dy, 1.65], [0.045, 0.045, 1.65], STEEL)
                box(f"{prefix}_guard_{dx}_{dy}", [x+dx, y+dy, 0.19], [0.085, 0.085, 0.19], YELLOW)
            for level in range(3):
                angle = math.atan2((-1) ** level, 0.92)
                box(f"{prefix}_brace_{dx}_{level}", [x + dx, y, 0.75 + level * 0.93],
                    [0.020, 0.019, 0.67], STEEL, False,
                    [math.cos(angle / 2), math.sin(angle / 2), 0, 0])
            for side in (-1, 1):
                box(f"{prefix}_sign_{side}_{dx}", [x + dx, y + side * 0.556, 1.47],
                    [0.125, 0.007, 0.13], WHITE, False)
        for tier, z in enumerate((0.30, 1.05, 1.80, 2.55)):
            box(f"{prefix}_deck_{tier}", [x, y, z], [1.10, 0.49, 0.035], STEEL)
            for side in (-1, 1):
                box(f"{prefix}_shelf_{tier}_{side}", [x, y + side * 0.50, z],
                    [1.10, 0.035, 0.066], ORANGE)
            for slot, dx in enumerate((-0.68, 0.0, 0.68)):
                dz = 0.14 + 0.035 * ((index + tier + slot) % 4)
                box(f"{prefix}_pallet_{tier}_{slot}", [x+dx, y, z+0.08], [0.28, 0.41, 0.025],
                    [0.49, 0.34, 0.21, 1])
                width = 0.21 + 0.025 * ((tier + slot) % 3)
                depth = 0.27 + 0.025 * ((index + slot) % 3)
                box(f"{prefix}_box_{tier}_{slot}", [x+dx, y, z+0.105+dz], [width, depth, dz],
                    [0.68 + 0.04 * (slot % 2), 0.49, 0.31, 1])

    # 후방 랙 사이 지름길은 쏟아진 적재물로 막혀 있다.
    for index, (x, y, z, hx, hy, hz, yaw) in enumerate((
        (0.50, 6.94, 0.24, 0.35, 0.31, 0.24, 0.21),
        (1.10, 7.03, 0.22, 0.38, 0.27, 0.22, -0.28),
        (1.76, 6.92, 0.25, 0.34, 0.31, 0.25, 0.30),
        (0.79, 6.97, 0.63, 0.27, 0.24, 0.18, -0.58),
        (1.43, 6.90, 0.62, 0.24, 0.24, 0.18, 0.43),
        (1.63, 7.55, 0.15, 0.18, 0.25, 0.15, -0.48),
        (0.73, 7.56, 0.13, 0.23, 0.18, 0.13, 0.62),
    )):
        box(f"spill_box_{index}", [x, y, z], [hx, hy, hz], [0.68, 0.49, 0.31, 1],
            quat=[math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)])
    # 비스듬히 남은 빈 팔레트. 포크가 들어갈 틈이 드러나도록 판재와 받침목을 분리한다.
    pallet_yaw = -0.36
    pallet_quat = [math.cos(pallet_yaw / 2), 0, 0, math.sin(pallet_yaw / 2)]
    for index, offset in enumerate((-0.27, -0.09, 0.09, 0.27)):
        x = 1.0 - offset * math.sin(pallet_yaw)
        y = 7.95 + offset * math.cos(pallet_yaw)
        box(f"spill_pallet_slat_{index}", [x, y, 0.10], [0.40, 0.065, 0.022],
            [0.45, 0.30, 0.17, 1], quat=pallet_quat)
    for index, offset in enumerate((-0.27, 0.27)):
        x = 1.0 + offset * math.cos(pallet_yaw)
        y = 7.95 + offset * math.sin(pallet_yaw)
        box(f"spill_pallet_block_{index}", [x, y, 0.039], [0.07, 0.33, 0.039],
            [0.36, 0.22, 0.12, 1], quat=pallet_quat)

    for y in (-2.8, -5.2):
        box(f"lane_{y}", [0, y, 0.006], [5.0, 0.033, 0.005], YELLOW, False)
    for stripe in range(7):
        box(f"crossing_{stripe}", [CROSS_X, -5.02 + stripe * 0.34, 0.009],
            [0.62, 0.093, 0.006], WHITE, False)
    for name, x, color in (("supply", -4.0, YELLOW), ("inspection", 4.0, [0.21, 0.70, 0.90, 1])):
        for side in (-1, 1):
            box(f"{name}_bay_{side}", [x, -4 + side * 0.72, 0.009], [0.85, 0.045, 0.006], color, False)
    for x in (-7.5, -5, -2.5, 0, 2.5, 5, 7.5, 10):
        box(f"ceiling_beam_{x}", [x, -0.35, 6.0], [0.11, 10.25, 0.13], STEEL, False)
        for y in (-8.3, -5.3, -2.3, 1.0, 4.3, 7.6):
            box(f"ceiling_fixture_{x}_{y}", [x, y, 5.55], [0.65, 0.095, 0.033], WHITE, False)
    box("ceiling_roof", [0.5, -0.35, 6.28], [11.5, 10.25, 0.08], [0.33, 0.32, 0.30, 1], False)
    for side, y in (("back", -10.55), ("aisle", 9.85)):
        box(f"warehouse_{side}_wall", [0.5, y, 3.1], [11.5, 0.09, 3.1], [0.65, 0.66, 0.64, 1])
        box(f"warehouse_{side}_skirting", [0.5, y + (0.10 if side == "back" else -0.10), 0.41],
            [11.5, 0.02, 0.41], STEEL, False)
        for x in (-7.5, -2.5, 2.5, 7.5):
            box(f"warehouse_column_{side}_{x}", [x, y + (0.22 if side == "back" else -0.22), 3.0],
                [0.14, 0.15, 3.0], STEEL)
    box("warehouse_end_wall", [11.7, -0.35, 3.1], [0.1, 10.25, 3.1], [0.65, 0.66, 0.64, 1])
    for y in (-7.6, -4.0, 0.1):
        box(f"warehouse_shutter_{y}", [11.58, y, 1.67], [0.025, 1.44, 1.67], STEEL, False)
        box(f"warehouse_shutter_header_{y}", [11.50, y, 3.39], [0.12, 1.53, 0.10], STEEL, False)
    # 도크 앞 대기 구획과 검수대는 AMR 통로와 횡단 동선 밖에 둔다.
    for index, y in enumerate((-8.9, -6.7, -4.5, -2.3, -0.1, 2.1)):
        box(f"dock_stripe_{index}", [9.5, y, 0.008], [1.65, 0.035, 0.005], YELLOW, False)
    for index, y in enumerate((-6.2, -1.1)):
        for dx in (-0.63, 0.63):
            box(f"inspection_leg_{index}_{dx}", [7.9 + dx, y, 0.41],
                [0.055, 0.27, 0.41], STEEL)
        box(f"inspection_bench_{index}", [7.9, y, 0.84], [0.77, 0.34, 0.048], STEEL)
        box(f"rack_{index}_box_inspection", [8.2, y, 1.04], [0.31, 0.23, 0.15], ORANGE)

    for name, pos, target, fovy in (
        ("cctv_1", (-9.25, -5.8, 4.75), (2.15, -3.6, 1.05), 65),
        ("cctv_2", (4.4, -3.2, 3.9), (1.0, -4.2, 0.65), 68),
        ("cctv_3", (10.6, -5.8, 4.65), (2.1, -3.7, 0.90), 70),
        ("cctv_4", (1.08, 9.55, 4.65), (1.15, 6.35, 0.45), 70),
    ):
        target_body = world.add_body(name=f"{name}_target", pos=target)
        if name in {"cctv_3", "cctv_4"}:
            world.add_camera(name=name, pos=pos, targetbody=target_body.name, fovy=fovy,
                             mode=mujoco.mjtCamLight.mjCAMLIGHT_TARGETBODY)
        else:
            camera = spec.camera(name)
            camera.pos, camera.targetbody, camera.fovy = pos, target_body.name, fovy

    add_plant_amrs(spec, WAREHOUSE_AMRS)
    spec.geom("amr:amr-02/chassis").rgba = [0.12, 0.52, 0.72, 1]
    spec.geom("amr:amr-03/chassis").rgba = [0.91, 0.44, 0.10, 1]
    chassis = spec.body(f"amr:{AMR.robot_id}")
    chassis.add_camera(name="amr_camera", pos=[0.62, 0, 0.50],
                       xyaxes=[0, -1, 0, 0.15, 0, 0.989], fovy=57)
