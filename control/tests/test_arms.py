"""로봇 팔 공통 계층(arms.py)과 MANIPULATE 사전 시뮬레이션 (이슈 #95).

모델은 저장소에 없으므로(pixi run fetch-models) 없으면 건너뛴다.
"""
import math

import mujoco
import numpy as np
import pytest

import arms
import twin_simulate as ts

pytestmark = pytest.mark.skipif(not all(m.path.is_file() for m in arms.ARM_MODELS.values()),
                                reason="robot arm models missing: pixi run fetch-models")


@pytest.mark.parametrize("key", sorted(arms.ARM_MODELS))
def test_every_model_picks_and_places_with_shared_planner(key):
    planner = arms.ArmPlanner(key, (0.0, 0.0, 0.30), 0.0)
    half = arms.TOOLS[arms.ARM_MODELS[key].tool].max_box / 2 - 0.02
    pick, place = (0.55, -0.2, half), (0.55, 0.2, half)
    p1 = planner.plan("PICK", pick, 0.25, planner.home)
    p2 = planner.plan("PLACE", place, 0.25, p1.points[-1].q, holding=True)
    assert p1.reached and p2.reached, (p1.reason, p2.reason)
    grasp = next(p for p in p1.points if p.phase == "GRASP")
    assert np.linalg.norm(planner.tcp(grasp.q) - pick) < arms.GOAL_TOLERANCE_M
    assert p1.points[-1].holding and not p2.points[-1].holding
    # 경로 점의 시간은 증가하고, TCP 속도는 speed_limit을 크게 넘지 않는다(첫 관절 보간 구간 제외)
    pts = [p for p in p1.points if p.phase != "APPROACH"]
    for a, b in zip(pts, pts[1:]):
        if b.t > a.t and a.phase == b.phase:
            v = np.linalg.norm(planner.tcp(b.q) - planner.tcp(a.q)) / (b.t - a.t)
            assert v < 0.25 * 1.3


def test_out_of_reach_is_not_reached():
    planner = arms.ArmPlanner("panda", (0.0, 0.0, 0.0), 0.0)
    plan = planner.plan("PICK", (2.0, 0.0, 0.1), 0.25, planner.home)
    assert not plan.reached and "out of reach" in plan.reason


def test_plant_scene_has_four_models_and_every_arm_moves_its_box():
    assert len({a.robot_model for a in arms.PLANT_ARMS}) >= 4
    results = arms.run_demo()
    assert all(ok and err < 0.03 for ok, err in results.values()), results


def test_plant_arms_hold_pose_under_physics_and_carry_box():
    model, data, kins = arms.build_plant_with_arms()
    pa = arms.PlantArms(model, data, kins)
    for _ in range(200):
        mujoco.mj_step(model, data)
    for kin in kins.values():
        assert np.abs(kin.get(data) - kin.home).max() < 0.05  # 위치 제어로 중력에 처지지 않음
    arm = arms.PLANT_ARMS[0]
    kin = kins[arm.robot_id]
    box = model.body(f"box:{arm.robot_id}").mocapid[0]
    data.mocap_pos[box] = kin.tcp(data)
    assert pa.set_gripper(data, arm.robot_id, True) == box
    q = kin.home.copy()
    q[0] += 0.3
    pa.set_target(data, arm.robot_id, q)
    for _ in range(500):
        mujoco.mj_step(model, data)
        pa.after_step(data)
    assert np.linalg.norm(data.mocap_pos[box] - kin.tcp(data)) < 1e-6
    pa.set_gripper(data, arm.robot_id, False)
    assert arm.robot_id not in pa.held


# ---- MANIPULATE 사전 시뮬레이션 ----

BASE = [0.0, 0.0, 0.45]
BOX = [0.6, 0.0, 0.05]


def manip_req(manipulation="PICK", target=BOX, workers=(), objects=None, holding=None, speed=0.25,
              model="ur5e", joints=None):
    robot = {"robot_id": "arm-01", "robot_model": model, "frame_id": "world", "position": BASE, "yaw_rad": 0.0}
    if holding:
        robot["holding_object_id"] = holding
    if joints is not None:
        robot["joint_positions"] = joints
    return {
        "contract_version": "backend-ctl/0.2", "proposal_id": "p-1", "snapshot_id": "snap-m",
        "snapshot_at": "2026-11-06T00:00:00Z",
        "action": {"action_type": "MANIPULATE", "robot_id": "arm-01", "speed_limit": speed,
                   "parameters": {"manipulation": manipulation, "object_id": "box-01",
                                  "target_pose": {"frame_id": "world", "position": list(target)}}},
        "state": {
            "robots": [robot],
            "workers": {"observed_at": "2026-11-05T23:59:59.800Z",
                        "items": [{"worker_id": w, "frame_id": "world", "position": [x, y, 0.0]}
                                  for w, x, y in workers]},
            "zones": [],
            "objects": objects if objects is not None else [
                {"object_id": "box-01", "frame_id": "world", "position": BOX, "size": [0.1, 0.1, 0.1]}],
        },
    }


def test_manipulate_pick_passes_without_workers():
    res = ts.simulate(manip_req())
    assert res["result"] == "PASS", res
    assert res["min_worker_distance_m"] is None
    assert res["simulated_horizon_s"] > 0


def test_manipulate_place_while_holding_passes():
    res = ts.simulate(manip_req("PLACE", target=[0.4, 0.4, 0.05], holding="box-01"))
    assert res["result"] == "PASS", res


def test_worker_near_arm_is_unsafe_distance():
    res = ts.simulate(manip_req(workers=(("worker-01", 0.9, 0.9),)))
    assert res["result"] == "UNSAFE_DISTANCE", res
    assert res["min_worker_distance_m"] < ts.UNSAFE_DISTANCE_M
    assert res["first_contact_at_s"] is None


def test_obstacle_over_target_is_collision():
    objects = [{"object_id": "box-01", "frame_id": "world", "position": BOX, "size": [0.1, 0.1, 0.1]},
               # 대상 바로 위 0.25 m에 놓인 판: 내려가는 공구가 닿는다
               {"object_id": "shelf", "frame_id": "world", "position": [0.6, 0.0, 0.3], "size": [0.4, 0.4, 0.02]}]
    res = ts.simulate(manip_req(objects=objects))
    assert res["result"] == "COLLISION", res
    assert res["first_contact_at_s"] is not None


def test_target_out_of_reach_is_goal_not_reached():
    far = [2.5, 0.0, 0.05]
    objects = [{"object_id": "box-01", "frame_id": "world", "position": far, "size": [0.1, 0.1, 0.1]}]
    res = ts.simulate(manip_req(target=far, objects=objects))
    assert res["result"] == "GOAL_NOT_REACHED", res


@pytest.mark.parametrize("change, detail", [
    (lambda r: r["state"]["robots"][0].pop("robot_model"), "robot_model"),
    (lambda r: r["action"]["parameters"].update(manipulation="THROW"), "PICK or PLACE"),
    (lambda r: r["action"]["parameters"].update(object_id="nope"), "state.objects"),
    (lambda r: r["action"]["parameters"]["target_pose"].update(position=[0.6, 0.3, 0.05]), "from object"),
    (lambda r: r["state"]["robots"][0].update(joint_positions=[0.0]), "joint_positions"),
])
def test_manipulate_invalid_requests_are_error(change, detail):
    r = manip_req()
    change(r)
    res = ts.simulate(r)
    assert res["result"] == "ERROR"
    assert detail in res["detail"]


def test_place_without_holding_is_error():
    res = ts.simulate(manip_req("PLACE"))
    assert res["result"] == "ERROR" and "does not hold" in res["detail"]


def test_move_to_still_uses_cylinder_and_ignores_objects_far_away():
    r = manip_req()
    r["state"]["robots"] = [{"robot_id": "robot-01", "robot_type": "MOBILE", "frame_id": "world",
                             "position": [-2.0, 0.0, 0.0], "yaw_rad": 0.0}]
    r["action"] = {"action_type": "MOVE_TO", "robot_id": "robot-01", "speed_limit": 0.5,
                   "target_pose": {"frame_id": "world", "position": [-1.0, -1.0, 0.0]}}
    assert ts.simulate(r)["result"] == "PASS"
    # 물체를 경로 위에 두면 이동 로봇도 부딪힌다
    r["state"]["objects"][0]["position"] = [-1.5, -0.5, 0.05]
    assert ts.simulate(r)["result"] == "COLLISION"
    assert math.isfinite(ts.simulate(r)["first_contact_at_s"])
