"""CRITICAL HazardEvent 등록 시 실행 중 로봇 즉시 정지(E-Stop 발행) 테스트. 이슈 #60."""

import unittest

from fastapi.testclient import TestClient

from app import main
from app.ctl_bridge import MockActionDispatcher
from app.main import app, seed_rules
from app.store import store
from tests.zone_fixtures import register_work_zones


class HazardEStopTests(unittest.TestCase):
    def setUp(self):
        store.reset()
        seed_rules()
        self.original_dispatcher = main.action_dispatcher
        self.dispatcher = MockActionDispatcher()
        main.action_dispatcher = self.dispatcher
        self.client = TestClient(app)
        register_work_zones(self.client, "A구역", "B구역")
        for robot_id in ("robot-01", "robot-02"):
            self._post("/robots", {
                "robot_id": robot_id, "robot_type": "MOBILE",
                "control_adapter": "mock-mobile-adapter", "status": "IDLE",
                "pose": {"frame_id": "map", "position": {"x": 0, "y": 0, "z": 0}},
                "velocity": {"x": 0, "y": 0, "z": 0},
                "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
            })

    def tearDown(self):
        main.action_dispatcher = self.original_dispatcher

    def _post(self, path, body=None, expected=201):
        response = self.client.post(path, json=body)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def execute(self, robot_id="robot-01"):
        task = self._post("/task-orders/from-natural-language",
                          {"instruction": f"{robot_id} 로봇을 A구역으로 이동해"})
        proposal = self._post("/action-proposals", {
            "task_id": task["task_id"], "robot_id": robot_id, "action_type": "MOVE_TO",
            "parameters": {"target_pose": {"type": "ZONE", "zone_id": "A구역"}, "speed_limit": 0.3},
            "trajectory": None, "frame_id": "map", "speed_limit": 0.3,
            "model_name": "mock-vla", "model_version": "test-0.1",
        })
        self._post(f"/action-proposals/{proposal['proposal_id']}/evaluate", expected=200)
        executed = self._post(f"/action-proposals/{proposal['proposal_id']}/execute", expected=200)
        self.assertEqual(executed["status"], "EXECUTING")
        return executed

    def hazard(self, severity="CRITICAL", robots=("robot-01",)):
        return self._post("/hazards", {
            "hazard_type": "WORKER_INTRUSION", "severity": severity,
            "source_observation_ids": ["cctv_1@1.0"], "related_robot_ids": list(robots),
            "related_worker_ids": [], "zone_id": "B구역", "confidence": 1.0,
        })

    def resolve(self, hazard):
        return self._post(f"/hazards/{hazard['hazard_id']}/resolve", {"resolved_by": "OPERATOR"}, 200)

    def test_critical_hazard_stops_executing_robot(self):
        self.execute()
        self.hazard()
        self.assertEqual(len(self.dispatcher.estop_commands), 1)
        command = self.dispatcher.estop_commands[0]
        self.assertTrue(command["active"])
        self.assertEqual(command["triggered_by"], "RULE_ENGINE")
        self.assertTrue(command["issued_at"].endswith("Z"))

    def test_idle_robot_is_not_stopped(self):
        self.hazard()
        self.assertEqual(self.dispatcher.estop_commands, [])

    def test_warning_and_unrelated_hazards_do_not_stop(self):
        self.execute()
        self.hazard(severity="WARNING")
        self.hazard(robots=("robot-02",))
        self.hazard(robots=())
        self.assertEqual(self.dispatcher.estop_commands, [])

    def test_release_only_after_every_triggering_hazard_is_resolved(self):
        self.execute()
        first, second = self.hazard(), self.hazard()
        self.resolve(first)
        self.assertEqual([c["active"] for c in self.dispatcher.estop_commands], [True, True])
        self.resolve(second)
        self.assertEqual([c["active"] for c in self.dispatcher.estop_commands], [True, True, False])

    def test_resolving_non_triggering_hazard_does_not_release(self):
        self.execute()
        self.hazard()
        self.resolve(self.hazard(severity="WARNING"))
        self.assertEqual([c["active"] for c in self.dispatcher.estop_commands], [True])

    def test_ctl_estop_event_holds_proposal_and_task(self):
        proposal = self.execute()
        self.hazard()
        main.apply_ctl_safety_event({"kind": "ESTOP", "robot_id": "robot-01", "proposal_id": None,
                                     "triggered_by": "RULE_ENGINE"})
        self.assertEqual(store.robots["robot-01"].status, "ESTOP")
        self.assertEqual(store.proposals[proposal["proposal_id"]].status, "HELD")
        self.assertEqual(store.tasks[proposal["task_id"]].status, "HELD")

    def test_dispatcher_without_estop_does_not_break_registration(self):
        class Legacy:
            name = "legacy"
        main.action_dispatcher = Legacy()
        store.proposals.clear()
        self.hazard()  # 발행할 수 없어도 위험 등록은 성공한다(이후 평가·재검증 차단은 그대로)

    def test_destination_zone_hazard_stops_executing_robot(self):
        self.execute("robot-01")  # 목적지 A구역
        response = self.client.post("/hazards", json={
            "hazard_type": "WORKER_INTRUSION", "severity": "CRITICAL",
            "source_observation_ids": ["cctv_1@1.0"], "related_robot_ids": [],
            "related_worker_ids": [], "zone_id": "A구역", "confidence": 1.0,
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual([c["active"] for c in self.dispatcher.estop_commands], [True])

    def test_delayed_activate_does_not_override_later_release(self):
        import threading
        self.execute("robot-01")
        entered, proceed = threading.Event(), threading.Event()
        original = self.dispatcher.publish_estop
        calls = {"n": 0}

        def slow_publish(active, triggered_by="RULE_ENGINE"):
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                proceed.wait(5)
            return original(active, triggered_by)

        self.dispatcher.publish_estop = slow_publish
        created = {}
        worker = threading.Thread(target=lambda: created.update(self.hazard()))
        worker.start()
        self.assertTrue(entered.wait(5))
        resolver = threading.Thread(target=lambda: self.client.post(
            f"/hazards/{created_id()}/resolve", json={"resolved_by": "OPERATOR"}))

        def created_id():
            with store.lock:
                return next(iter(store.hazards))

        resolver.start()
        proceed.set()
        worker.join(5)
        resolver.join(5)
        values = [c["active"] for c in self.dispatcher.estop_commands]
        self.assertEqual(values[-1], False)
        self.assertEqual(store.estop_hazard_ids, set())


if __name__ == "__main__":
    unittest.main()
