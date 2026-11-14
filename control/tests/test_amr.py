"""이동형 운반 로봇(AMR): 장면 구성, 운동학 이동·싣기·내리기, 사전 시뮬레이션 차체 크기."""
import math

import mujoco
import numpy as np
import pytest

import amr
import arms
import twin_simulate as ts

DT = 0.02


@pytest.fixture(scope="module")
def plant_only():
    """팔 없이 plant_scene + AMR (팔 모델 없이도 돈다)."""
    return amr.build_plant_with_amrs(with_arms=False)


def fresh():
    model, data, _, plant = amr.build_plant_with_amrs(with_arms=False)
    return model, data, plant


def settle(plant, data, seconds):
    for _ in range(int(seconds / DT) + 1):
        plant.step(data, DT)


def test_scene_has_two_amrs_with_pallets_on_warehouse_stands(plant_only):
    model, data, _, plant = plant_only
    assert sorted(plant.mocap) == ["amr-01", "amr-02"]
    for a in amr.PLANT_AMRS:
        pallet = data.mocap_pos[plant.pallets[a.pallet_id]]
        assert np.allclose(pallet, a.place_pose(a.warehouse_stand))
        model.geom(f"stand:{a.robot_id}:cell/top")
    # 팔·plant_scene 기존 요소는 그대로 있다
    model.body("worker")


def test_layout_crosses_worker_aisle_and_stays_clear_of_arm_pallets():
    for a in amr.PLANT_AMRS:
        y = a.warehouse_dock[1]
        assert a.cell_dock[1] == y  # 직선 통로
        lo, hi = sorted((a.cell_dock[0], a.warehouse_dock[0]))
        assert lo < 3.4 < hi and abs(abs(y) - 1.8) < 0.1  # 작업자 1 경로점 (3.4, ±1.8)을 지난다
        for arm in arms.PLANT_ARMS:
            if arm.box_half != arms.BIG_BOX_HALF:
                continue
            px, py = arm.place[:2]
            # 실린 팔레트 반폭 + 팔 팔레트 반폭보다 통로 중심선이 멀다
            assert abs(py - y) > amr.PALLET_HALF[1] + arms.PALLET_HALF[1] or not (lo - 1 < px < hi + 1)


def test_pick_drive_place_moves_pallet_kinematically():
    model, data, plant = fresh()
    a = amr.PLANT_AMRS[0]
    plant.load(data, a.robot_id, {"command_id": "c1", "manipulation": "PICK", "object_id": a.pallet_id,
                                  "target_pose": {"position": list(a.place_pose(a.warehouse_stand))}})
    plant.set_velocity(a.robot_id, 0.4, 0.0)  # 이송 중 이동 명령은 무시
    settle(plant, data, amr.TRANSFER_S)
    assert plant.pop_results() == [(a.robot_id, {"command_id": "c1", "success": True, "reason_code": None,
                                                  "message": "PICK (싣기) 완료"})]
    assert math.dist(plant.pose[a.robot_id][:2], a.warehouse_dock[:2]) < 0.05
    assert amr.drive_to(plant, model, data, a.robot_id, a.cell_dock[:2])
    amr.turn_to(plant, data, a.robot_id, a.cell_dock[2] + 0.3)  # 도착 방향이 어긋나 있어도
    pallet = plant.pallets[a.pallet_id]
    assert np.allclose(data.mocap_pos[pallet][:2], plant.pose[a.robot_id][:2])  # 차체를 따라온다
    assert data.mocap_pos[pallet][2] == pytest.approx(amr.DECK_TOP)
    plant.load(data, a.robot_id, {"command_id": "c2", "manipulation": "PLACE", "object_id": a.pallet_id,
                                  "target_pose": {"position": list(a.place_pose(a.cell_stand))}})
    settle(plant, data, amr.TRANSFER_S + 0.5)  # 먼저 거치대 쪽으로 돌고 내린다
    assert plant.pop_results()[0][1]["success"]
    assert plant.pose[a.robot_id][2] == pytest.approx(a.cell_dock[2], abs=0.02) or \
        abs(abs(plant.pose[a.robot_id][2]) - math.pi) < 0.02
    assert np.linalg.norm(data.mocap_pos[pallet] - a.place_pose(a.cell_stand)) < 0.05
    assert a.robot_id not in plant.held
    # 내린 뒤 차체가 떠나도 팔레트는 남는다
    amr.drive_to(plant, model, data, a.robot_id, a.warehouse_dock[:2])
    assert np.linalg.norm(data.mocap_pos[pallet] - a.place_pose(a.cell_stand)) < 0.05


@pytest.mark.parametrize("cmd, reason", [
    ({"manipulation": "PICK", "object_id": "pallet:amr-02"}, "INVALID"),  # 다른 거치대의 팔레트 (멀리 있음)
    ({"manipulation": "PLACE", "object_id": "pallet:amr-01", "target_pose": {"position": [0, 0, 0.3]}}, "INVALID"),
    ({"manipulation": "PUSH", "object_id": "pallet:amr-01"}, "UNSUPPORTED"),
])
def test_rejects_bad_load_commands(cmd, reason):
    _, data, plant = fresh()
    plant.load(data, "amr-01", {"command_id": "c", **cmd})
    (rid, result), = plant.pop_results()
    assert rid == "amr-01" and not result["success"] and result["reason_code"] == reason


def test_cancel_stops_transfer_and_cmd_timeout_stops_chassis():
    _, data, plant = fresh()
    a = amr.PLANT_AMRS[0]
    plant.load(data, a.robot_id, {"command_id": "c", "manipulation": "PICK", "object_id": a.pallet_id})
    settle(plant, data, 0.5)
    plant.load(data, a.robot_id, {"command_id": "c", "manipulation": "CANCEL"})
    assert plant.pop_results()[-1][1]["reason_code"] == "CANCELLED"
    assert a.robot_id not in plant.held and a.robot_id not in plant.transfer
    plant.set_velocity(a.robot_id, 0.3, 0.0)
    x0 = plant.pose[a.robot_id][0]
    settle(plant, data, 2.0)
    assert plant.pose[a.robot_id][0] - x0 == pytest.approx(0.3 * 0.5, abs=0.03)  # 0.5 s 뒤 정지


def test_twin_uses_larger_footprint_when_loaded():
    assert amr.footprint_radius(True) > amr.footprint_radius(False)
    assert amr.footprint_radius(True) == pytest.approx(math.hypot(*amr.PALLET_HALF))
    assert amr.footprint_height(True) > 1.0 > amr.footprint_height(False)


def move_req(holding, worker_y):
    robot = {"robot_id": "amr-01", "robot_type": "MOBILE", "robot_model": "amr", "frame_id": "world",
             "position": [3.7, 1.75, 0.0], "yaw_rad": math.pi}
    if holding:
        robot["holding_object_id"] = "pallet:amr-01"
    return {"contract_version": "backend-ctl/0.2", "proposal_id": "p", "snapshot_id": "s",
            "snapshot_at": "2026-11-06T00:00:00Z",
            "action": {"action_type": "MOVE_TO", "robot_id": "amr-01",
                       "target_pose": {"frame_id": "world", "position": [2.05, 1.75, 0.0]}, "speed_limit": 0.4},
            "state": {"robots": [robot], "zones": [],
                      "workers": {"observed_at": "2026-11-06T00:00:00Z",
                                  "items": [{"worker_id": "w1", "frame_id": "world",
                                             "position": [3.0, worker_y, 0.0]}]}}}


def test_twin_move_to_counts_loaded_pallet():
    # 통로 옆 작업자: 빈 차체(반경 0.558)는 닿지 않고, 실린 팔레트(반경 0.692)는 여유 반경 캡슐에 닿는다
    y = 1.75 + amr.footprint_radius(False) + 0.25 + 0.20 + 0.06
    empty = ts.simulate(move_req(False, y))
    loaded = ts.simulate(move_req(True, y))
    assert empty["result"] == "UNSAFE_DISTANCE", empty
    assert loaded["result"] == "COLLISION", loaded
    assert loaded["min_worker_distance_m"] < empty["min_worker_distance_m"]
