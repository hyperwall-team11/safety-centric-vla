"""창고 시연의 별도 구역에서 반복 운행하는 지게차와 견인차."""
import math

import mujoco

from amr import _add_pallet

FORK_X, FORK_START_Y, FORK_DOCK_Y = 9.15, -6.75, -4.45
FORK_PERIOD = 28.0
TUG_CENTER = (-7.35, 5.2)
TUG_RADIUS = 2.2
TUG_PERIOD = 36.0
HITCH_OFFSET = 0.43
FORK_MIN_HEIGHT, FORK_MAX_HEIGHT = 0.03, 0.28
FORK_LIFT_RATE = 0.125  # m/s, 시연용 포크 승강 속도
SUPPLY_STOPS = (0.0, 18.0)
SUPPLY_TABLES = ((-4.62, 4.07), (-10.08, 6.33))

YELLOW = [0.96, 0.69, 0.10, 1]
STEEL = [0.23, 0.29, 0.32, 1]
DARK = [0.08, 0.10, 0.11, 1]
TEAL = [0.11, 0.54, 0.55, 1]


def forklift_pose(seconds):
    """포크 삽입 → 들어 올리기 → 도크 이동 → 내려놓기 → 회수."""
    t = seconds % FORK_PERIOD
    keyframes = (
        (0.0, FORK_START_Y, 0.03), (2.0, FORK_START_Y + 0.5, 0.03),
        (4.0, FORK_START_Y + 0.5, 0.28), (8.0, FORK_DOCK_Y, 0.28),
        (10.0, FORK_DOCK_Y, 0.03), (12.0, FORK_DOCK_Y - 0.50, 0.03),
        (16.0, FORK_DOCK_Y - 0.50, 0.03), (18.0, FORK_DOCK_Y, 0.03),
        (20.0, FORK_DOCK_Y, 0.28), (24.0, FORK_START_Y + 0.5, 0.28),
        (26.0, FORK_START_Y + 0.5, 0.03), (FORK_PERIOD, FORK_START_Y, 0.03),
    )
    for (start, y0, h0), (end, y1, h1) in zip(keyframes, keyframes[1:]):
        if t <= end:
            u = (t - start) / (end - start)
            y, height = y0 + u * (y1 - y0), h0 + u * (h1 - h0)
            if t < 2.0 or t >= 26.0:
                load_y = FORK_START_Y + 1.46
            elif 10.0 <= t <= 18.0:
                load_y = FORK_DOCK_Y + 0.96
            else:
                load_y = y + 0.96
            return y, height, load_y
    raise AssertionError("지게차 시각이 주기 범위 밖입니다")


def tug_pose(seconds, cart=0):
    """원형 공급 동선을 돌며 두 공급 지점에 정차한다."""
    t = seconds % TUG_PERIOD
    stopped = min(t, 3.0) + max(0.0, min(t - 18.0, 3.0))
    angle = (t - stopped) * math.tau / (TUG_PERIOD - 6.0) - cart * 0.56
    return (TUG_CENTER[0] + TUG_RADIUS * math.cos(angle),
            TUG_CENTER[1] + TUG_RADIUS * math.sin(angle), angle + math.pi / 2)



def hitch_pose(seconds, cart):
    """앞차 후면과 뒤차 전면의 연결점 중간과 연결봉 방향."""
    lead_x, lead_y, lead_yaw = tug_pose(seconds, cart - 1)
    cart_x, cart_y, cart_yaw = tug_pose(seconds, cart)
    rear = (lead_x - HITCH_OFFSET * math.cos(lead_yaw),
            lead_y - HITCH_OFFSET * math.sin(lead_yaw))
    front = (cart_x + HITCH_OFFSET * math.cos(cart_yaw),
             cart_y + HITCH_OFFSET * math.sin(cart_yaw))
    dx, dy = rear[0] - front[0], rear[1] - front[1]
    return ((front[0] + rear[0]) / 2, (front[1] + rear[1]) / 2,
            math.atan2(dy, dx), math.hypot(dx, dy))


def supply_parcel_pose(seconds, station):
    """첫 순회에서 상자를 내리고, 다음 순회 때 같은 정차 구간에서 회수한다."""
    x, y, yaw = tug_pose(seconds, cart=1)
    offset = 0.18 if station == 0 else -0.18
    load = (x + offset * math.cos(yaw), y + offset * math.sin(yaw), 0.61)
    table = (*SUPPLY_TABLES[station], 0.71)
    elapsed = seconds % (2 * TUG_PERIOD) - SUPPLY_STOPS[station]
    unload = min(1.0, max(0.0, elapsed / 3.0))
    reload = min(1.0, max(0.0, (elapsed - TUG_PERIOD) / 3.0))
    fraction = unload - reload
    return tuple(a + fraction * (b - a) for a, b in zip(load, table))

def add_warehouse_vehicles(spec):
    world = spec.worldbody

    def box(body, name, pos, size, color):
        body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=pos, size=size,
                      rgba=color, contype=0, conaffinity=0)

    def wheels(body, name, xs, track, radius):
        for i, x in enumerate(xs):
            for side in (-1, 1):
                body.add_geom(name=f"{name}_wheel_{i}_{side}", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                              pos=[x, side * track, radius], size=[radius, 0.064],
                              quat=[math.sqrt(0.5), math.sqrt(0.5), 0, 0],
                              rgba=DARK, contype=0, conaffinity=0)

    fork = world.add_body(name="warehouse_forklift", mocap=True, pos=[FORK_X, FORK_START_Y, 0])
    box(fork, "forklift_counterweight", [-0.32, 0, 0.30], [0.33, 0.39, 0.23], YELLOW)
    box(fork, "forklift_chassis", [0.06, 0, 0.19], [0.54, 0.38, 0.11], YELLOW)
    box(fork, "forklift_sensor", [-0.03, 0, 0.62], [0.17, 0.23, 0.09], STEEL)
    for side in (-1, 1):
        box(fork, f"forklift_mast_{side}", [0.59, side * 0.24, 0.69], [0.05, 0.055, 0.65], STEEL)
    box(fork, "forklift_mast_top", [0.59, 0, 1.29], [0.06, 0.28, 0.055], STEEL)
    wheels(fork, "forklift", (-0.44, 0.36), 0.37, 0.14)

    carriage = world.add_body(name="warehouse_forks", mocap=True, pos=[FORK_X, FORK_START_Y, 0.03])
    box(carriage, "forklift_carriage", [0.64, 0, 0.12], [0.06, 0.30, 0.14], STEEL)
    for side in (-1, 1):
        box(carriage, f"forklift_tine_{side}", [0.92, side * 0.19, 0], [0.39, 0.054, 0.025], STEEL)
    _add_pallet(world, "pallet:forklift", (FORK_X, FORK_START_Y + 1.46, 0.0))

    for cart in range(4):
        x, y, _ = tug_pose(0, cart)
        name = "warehouse_tug" if cart == 0 else f"warehouse_cart_{cart}"
        body = world.add_body(name=name, mocap=True, pos=[x, y, 0])
        color = TEAL if cart == 0 else STEEL
        box(body, f"{name}_frame", [0, 0, 0.19], [0.43, 0.30, 0.09], color)
        wheels(body, name, (-0.27, 0.29), 0.30, 0.10)
        if cart == 0:
            box(body, "tug_control_housing", [0.13, 0, 0.35], [0.24, 0.24, 0.13], TEAL)
            box(body, "tug_status_light", [0.14, 0, 0.50], [0.075, 0.055, 0.017], YELLOW)
        else:
            hx, hy, _, length = hitch_pose(0, cart)
            hitch = world.add_body(name=f"warehouse_hitch_{cart}", mocap=True, pos=[hx, hy, 0.14])
            box(hitch, f"{name}_coupling", [0, 0, 0], [length / 2 + 0.015, 0.035, 0.032], STEEL)
            for side in (-1, 1):
                box(body, f"{name}_bin_{side}", [0, side * 0.13, 0.41], [0.32, 0.12, 0.135], YELLOW)
    for index, (x, y) in enumerate(SUPPLY_TABLES):
        parcel = world.add_body(name=f"tug_parcel_{index}", mocap=True, pos=supply_parcel_pose(0, index))
        box(parcel, f"pallet:tug/cargo_{index}", [0, 0, 0], [0.105, 0.10, 0.073], [0.68, 0.49, 0.31, 1])
        box(world, f"tug_station_post_{index}", [x, y, 0.30], [0.08, 0.22, 0.30], STEEL)
        box(world, f"tug_station_deck_{index}", [x, y, 0.60], [0.19, 0.38, 0.04], TEAL)


class WarehouseVehicles:
    def __init__(self, model, data, autoplay=True):
        self.running = {"forklift": autoplay, "tug": autoplay}
        self.elapsed = dict.fromkeys(self.running, 0.0)
        self.estop_active = False
        self.fork_height = FORK_MIN_HEIGHT
        self.fork_target = FORK_MIN_HEIGHT
        self.last_time = float(data.time)
        self.odometry = {}
        self.previous_pose = {}
        names = ("warehouse_forklift", "warehouse_forks", "pallet:forklift",
                 "warehouse_tug", *(f"warehouse_cart_{i}" for i in range(1, 4)))
        self.ids = [model.body_mocapid[model.body(name).id] for name in names]
        self.hitches = [model.body_mocapid[model.body(f"warehouse_hitch_{i}").id] for i in range(1, 4)]
        self.parcels = [model.body_mocapid[model.body(f"tug_parcel_{i}").id] for i in range(2)]
        self.update(data)

    def set_running(self, vehicle, enabled):
        if enabled and self.estop_active:
            return False
        self.running[vehicle] = enabled
        if vehicle == "forklift" and not enabled:
            self.fork_target = self.fork_height
        return True

    def set_estop(self, active):
        self.estop_active = active
        if active:
            for vehicle in self.running:
                self.set_running(vehicle, False)

    def set_fork_height(self, height):
        if self.estop_active or self.running["forklift"] or not math.isfinite(height):
            return False
        if not FORK_MIN_HEIGHT <= height <= FORK_MAX_HEIGHT:
            return False
        self.fork_target = height
        return True

    def _record_odometry(self, name, pose, dt):
        x, y, yaw = pose
        vx = wz = 0.0
        if dt > 0 and name in self.previous_pose:
            x0, y0, yaw0 = self.previous_pose[name]
            vx = ((x - x0) * math.cos(yaw) + (y - y0) * math.sin(yaw)) / dt
            wz = math.remainder(yaw - yaw0, math.tau) / dt
        self.previous_pose[name] = pose
        self.odometry[name] = (*pose, vx, wz)

    def update(self, data):
        def set_pose(mocap, x, y, z, yaw):
            data.mocap_pos[mocap] = [x, y, z]
            data.mocap_quat[mocap] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]

        dt = max(0.0, float(data.time) - self.last_time)
        self.last_time = float(data.time)
        if self.running["tug"]:
            self.elapsed["tug"] += dt
        tug_time = self.elapsed["tug"]

        fork_time = self.elapsed["forklift"]
        y, nominal_height, load_y = forklift_pose(fork_time)
        if self.running["forklift"] and math.isclose(self.fork_height, nominal_height, abs_tol=1e-9):
            fork_time += dt
            self.elapsed["forklift"] = fork_time
            y, self.fork_height, load_y = forklift_pose(fork_time)
            self.fork_target = self.fork_height
        else:
            target = nominal_height if self.running["forklift"] else self.fork_target
            error = target - self.fork_height
            self.fork_height += math.copysign(min(abs(error), FORK_LIFT_RATE * dt), error)
        height = self.fork_height
        load_z = max(0.0, height - FORK_MIN_HEIGHT) if abs(load_y - y - 0.96) < 0.08 else 0.0
        self._record_odometry("forklift", (FORK_X, y, math.pi / 2), dt)
        self._record_odometry("tug", tug_pose(tug_time), dt)
        set_pose(self.ids[0], FORK_X, y, 0, math.pi / 2)
        set_pose(self.ids[1], FORK_X, y, height, math.pi / 2)
        set_pose(self.ids[2], FORK_X, load_y, load_z, math.pi / 2)
        for cart, mocap in enumerate(self.ids[3:]):
            x, y, yaw = tug_pose(tug_time, cart)
            set_pose(mocap, x, y, 0, yaw)
        for cart, mocap in enumerate(self.hitches, start=1):
            x, y, yaw, _ = hitch_pose(tug_time, cart)
            set_pose(mocap, x, y, 0.14, yaw)
        for station, mocap in enumerate(self.parcels):
            x, y, z = supply_parcel_pose(tug_time, station)
            set_pose(mocap, x, y, z, 0)
