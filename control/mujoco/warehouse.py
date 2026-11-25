"""최종 발표용 창고 배치와 AMR 위치로 시작하는 단발 작업자 횡단."""
import math

import mujoco

from amr import PlantAmr, add_plant_amrs
from scenario import WorkerScript

AMR = PlantAmr("amr-01", "pallet:amr-01", (-4.0, -4.0, 0.0), (4.0, -4.0, 0.0))
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


def add_warehouse(spec):
    world = spec.worldbody
    for geom in list(world.geoms):
        if geom.name.startswith("prop_") or geom.name in {"keep_out_zone", "zone_a_marking", "zone_b_marking"}:
            spec.delete(geom)
    for name in ("worker_2", "worker_3"):
        spec.delete(spec.body(name))

    def box(name, pos, size, color, collidable=True):
        world.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=pos, size=size, rgba=color,
                       contype=int(collidable), conaffinity=int(collidable))

    for index, (x, y) in enumerate(((-3.5, -1.5), (-0.9, -1.5), (3.1, -1.5),
                                     (-3.3, -7.3), (-0.7, -7.3), (3.1, -7.3))):
        prefix = f"rack_{index}"
        for dx in (-1.08, 1.08):
            for dy in (-0.50, 0.50):
                box(f"{prefix}_post_{dx}_{dy}", [x+dx, y+dy, 1.65], [0.045, 0.045, 1.65], STEEL)
                box(f"{prefix}_guard_{dx}_{dy}", [x+dx, y+dy, 0.19], [0.085, 0.085, 0.19], YELLOW)
        for tier, z in enumerate((0.30, 1.05, 1.80, 2.55)):
            box(f"{prefix}_shelf_{tier}", [x, y, z], [1.10, 0.52, 0.05], ORANGE)
            for slot, dx in enumerate((-0.68, 0.0, 0.68)):
                dz = 0.14 + 0.035 * ((index + tier + slot) % 4)
                box(f"{prefix}_pallet_{tier}_{slot}", [x+dx, y, z+0.08], [0.28, 0.41, 0.025],
                    [0.49, 0.34, 0.21, 1])
                box(f"{prefix}_box_{tier}_{slot}", [x+dx, y, z+0.105+dz], [0.26, 0.34, dz],
                    [0.68 + 0.04 * (slot % 2), 0.49, 0.31, 1])

    for y in (-2.8, -5.2):
        box(f"lane_{y}", [0, y, 0.006], [5.0, 0.033, 0.005], YELLOW, False)
    for stripe in range(7):
        box(f"crossing_{stripe}", [CROSS_X, -5.02 + stripe * 0.34, 0.009],
            [0.62, 0.093, 0.006], WHITE, False)
    for name, x, color in (("supply", -4.0, YELLOW), ("inspection", 4.0, [0.21, 0.70, 0.90, 1])):
        for side in (-1, 1):
            box(f"{name}_bay_{side}", [x, -4 + side * 0.72, 0.009], [0.85, 0.045, 0.006], color, False)
    for x in (-4, -1, 2, 5):
        box(f"ceiling_fixture_{x}", [x, -4.0, 5.1], [0.68, 0.13, 0.04], WHITE)
        world.add_light(name=f"warehouse_light_{x}", pos=[x, -4, 5], dir=[0, 0, -1])
    box("warehouse_back_wall", [0, -8.5, 2.6], [7, 0.1, 2.6], STEEL)
    box("warehouse_end_wall", [6.3, -4.0, 2.6], [0.1, 4.5, 2.6], STEEL)

    for name, pos, target, fovy in (
        ("cctv_1", (-5.5, -6.2, 4.2), (0.2, -3.9, 0.15), 62),
        ("cctv_2", (4.4, -3.2, 3.9), (1.0, -4.2, 0.65), 68),
    ):
        target_body = world.add_body(name=f"{name}_target", pos=target)
        camera = spec.camera(name)
        camera.pos, camera.targetbody, camera.fovy = pos, target_body.name, fovy

    add_plant_amrs(spec, (AMR,))
    chassis = spec.body(f"amr:{AMR.robot_id}")
    chassis.add_camera(name="amr_camera", pos=[0.62, 0, 0.50],
                       xyaxes=[0, -1, 0, 0.15, 0, 0.989], fovy=57)
