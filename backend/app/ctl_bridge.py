"""Backend ActionProposal과 ROS 2 제어 어댑터 사이의 전송 경계."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, Protocol
from urllib import error, request

from .models import ActionProposal, AdapterResultReport, Robot, Zone


LOGGER = logging.getLogger(__name__)
CONTRACT_VERSION = "backend-ctl/0.1"
APPROVED_ACTION_TOPIC = "/safety/approved_action"
HEARTBEAT_TOPIC = "/safety/heartbeat"
ESTOP_TOPIC = "/safety/estop"
LOCAL_FAILURE_CODES = {
    "UNSUPPORTED": "UNSUPPORTED",
    "NOT_APPROVED": "UNSUPPORTED",
    "INVALID": "UNSUPPORTED",
    "TIMEOUT": "TIMEOUT",
    "ROBOT_BUSY": "TIMEOUT",
    "ROBOT_HELD": "TIMEOUT",
    "NO_HEARTBEAT": "TIMEOUT",
    "CANCELLED": "TIMEOUT",
    "ESTOP": "TIMEOUT",
    "HW_ERROR": "HW_ERROR",
}


class DispatchError(RuntimeError):
    """승인 명령을 제어 어댑터에 전달하지 못한 경우."""


class CommandSerializationError(DispatchError):
    """Proposal을 실행 가능한 CTL 명령으로 만들 수 없는 경우."""


class ActionDispatcher(Protocol):
    name: str

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def dispatch(
        self,
        proposal: ActionProposal,
        robot: Robot,
        zones: list[Zone],
    ) -> dict[str, Any] | None: ...

    def publish_estop(self, active: bool, triggered_by: str = "RULE_ENGINE") -> dict[str, Any]: ...


def build_estop_command(active: bool, triggered_by: str = "RULE_ENGINE") -> dict[str, Any]:
    """제어 계약 6장 E-Stop 명령. 해제(`active=false`)는 정지 신호만 풀고 복귀는 RESUME으로 한다."""
    return {
        "active": bool(active),
        "triggered_by": triggered_by,
        "issued_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def topic_segment(robot_id: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_]", "_", robot_id)
    return f"r_{segment}" if segment[:1].isdigit() else segment


def result_topic(robot_id: str) -> str:
    return f"/adapter/{topic_segment(robot_id)}/result"


def _position_list(position: Any) -> list[float]:
    if not isinstance(position, dict):
        raise CommandSerializationError("좌표 목적지 position은 {x,y,z} 형식이어야 합니다.")
    try:
        return [float(position[axis]) for axis in ("x", "y", "z")]
    except (KeyError, TypeError, ValueError) as exc:
        raise CommandSerializationError(
            "좌표 목적지 position에 유효한 x, y, z가 필요합니다."
        ) from exc


def _serialize_target_pose(
    target_pose: dict[str, Any],
    proposal: ActionProposal,
    zones: list[Zone],
) -> dict[str, Any]:
    target_type = target_pose.get("type")
    if target_type == "COORDINATE":
        return {
            "frame_id": target_pose.get("frame_id") or proposal.frame_id,
            "position": _position_list(target_pose.get("position")),
        }
    if target_type == "ZONE":
        zone_id = target_pose.get("zone_id")
        zone = next((item for item in zones if item.zone_id == zone_id), None)
        if zone is None:
            raise CommandSerializationError(f"목적지 Zone을 찾을 수 없습니다: {zone_id}")
        navigation_target = zone.geometry.get("navigation_target")
        if navigation_target is None:
            raise CommandSerializationError(
                f"Zone.geometry.navigation_target이 필요합니다: {zone_id}"
            )
        return {
            "frame_id": zone.frame_id,
            "position": _position_list(navigation_target),
        }
    raise CommandSerializationError(f"지원하지 않는 목적지 형식입니다: {target_type}")


def build_approved_action_command(
    proposal: ActionProposal,
    robot: Robot,
    zones: list[Zone],
) -> dict[str, Any]:
    """스키마 객체를 CTL이 실행할 수 있는 불변 승인 스냅샷으로 변환한다."""
    parameters = deepcopy(proposal.parameters)
    target_pose = parameters.get("target_pose")
    if target_pose is not None:
        if not isinstance(target_pose, dict):
            raise CommandSerializationError("target_pose는 객체여야 합니다.")
        parameters["target_pose"] = _serialize_target_pose(
            target_pose,
            proposal,
            zones,
        )

    speed_candidates = [proposal.speed_limit, robot.motion_limits.max_speed]
    parameter_speed = parameters.get("speed_limit")
    if isinstance(parameter_speed, (int, float)) and not isinstance(parameter_speed, bool):
        speed_candidates.append(float(parameter_speed))
    speed_limit = min(speed_candidates)
    if "speed_limit" in parameters:
        parameters["speed_limit"] = speed_limit

    return {
        "contract_version": CONTRACT_VERSION,
        "proposal_id": proposal.proposal_id,
        "task_id": proposal.task_id,
        "robot_id": proposal.robot_id,
        "action_type": proposal.action_type,
        "parameters": parameters,
        "trajectory": deepcopy(proposal.trajectory),
        "frame_id": proposal.frame_id,
        "speed_limit": speed_limit,
        "model_name": proposal.model_name,
        "model_version": proposal.model_version,
        "status": "APPROVED",
        "proposed_at": proposal.proposed_at.isoformat().replace("+00:00", "Z"),
    }


def result_event_to_report(event: dict[str, Any]) -> AdapterResultReport:
    if event.get("kind") != "RESULT":
        raise ValueError("RESULT 이벤트만 실행 결과로 변환할 수 있습니다.")
    success = event.get("success")
    if success is True:
        return AdapterResultReport(status="SUCCEEDED", detail=event.get("message"))
    if success is not False:
        raise ValueError("RESULT 이벤트의 success는 boolean이어야 합니다.")

    local_code = event.get("reason_code")
    standard_code = LOCAL_FAILURE_CODES.get(local_code)
    if standard_code is None:
        raise ValueError(f"알 수 없는 CTL reason_code입니다: {local_code}")
    message = event.get("message") or "제어 어댑터 실행 실패"
    detail = message if local_code == standard_code else f"[{local_code}] {message}"
    return AdapterResultReport(
        status="FAILED",
        reason_code=standard_code,
        detail=detail,
    )


class MockActionDispatcher:
    """ROS 2를 사용하지 않는 로컬 개발·단위 테스트용 전송기."""

    name = "mock"

    def __init__(self) -> None:
        self.estop_commands: list[dict[str, Any]] = []

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def dispatch(
        self,
        proposal: ActionProposal,
        robot: Robot,
        zones: list[Zone],
    ) -> None:
        return None

    def publish_estop(self, active: bool, triggered_by: str = "RULE_ENGINE") -> dict[str, Any]:
        command = build_estop_command(active, triggered_by)
        self.estop_commands.append(command)
        return command


class Ros2ActionDispatcher:
    """FastAPI 프로세스 안에서 ROS 2 publish·subscribe를 담당한다.

    ROS 의존성은 기능을 켰을 때만 import하므로 일반 Backend 테스트 환경에는
    rclpy 설치가 필요하지 않다.
    """

    name = "ros2"

    def __init__(
        self,
        result_api_base: str = "http://127.0.0.1:8000",
        safety_event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.result_api_base = result_api_base.rstrip("/")
        self.safety_event_handler = safety_event_handler
        self._node: Any = None
        self._rclpy: Any = None
        self._string_type: Any = None
        self._executor_thread: threading.Thread | None = None
        self._subscriptions: dict[str, Any] = {}
        self._proposal_robots: dict[str, str] = {}
        self._lock = threading.RLock()

    def start(self) -> None:
        if self._node is not None:
            return
        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
            from std_msgs.msg import String
        except ImportError as exc:
            raise DispatchError(
                "ROS 2 모드에는 rclpy와 std_msgs가 필요합니다. control의 pixi 환경에서 실행하세요."
            ) from exc

        if not rclpy.ok():
            rclpy.init(args=None)
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        node = Node("backend_ctl_bridge")
        self._rclpy = rclpy
        self._string_type = String
        self._qos = qos
        self._node = node
        self._action_publisher = node.create_publisher(String, APPROVED_ACTION_TOPIC, qos)
        self._heartbeat_publisher = node.create_publisher(String, HEARTBEAT_TOPIC, qos)
        # control safety_control.interfaces.ESTOP_QOS와 같아야 늦게 뜬 어댑터도 마지막 정지 상태를 받는다.
        estop_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._estop_publisher = node.create_publisher(String, ESTOP_TOPIC, estop_qos)
        node.create_timer(0.2, self._publish_heartbeat)
        self._executor_thread = threading.Thread(
            target=rclpy.spin,
            args=(node,),
            name="backend-ctl-ros2",
            daemon=True,
        )
        self._executor_thread.start()
        LOGGER.info("Backend–CTL ROS 2 브리지를 시작했습니다.")

    def stop(self) -> None:
        node = self._node
        if node is None:
            return
        node.destroy_node()
        if self._rclpy is not None and self._rclpy.ok():
            self._rclpy.try_shutdown()
        if self._executor_thread is not None:
            self._executor_thread.join(timeout=2)
        self._node = None

    def dispatch(
        self,
        proposal: ActionProposal,
        robot: Robot,
        zones: list[Zone],
    ) -> dict[str, Any]:
        if self._node is None:
            raise DispatchError("ROS 2 브리지가 시작되지 않았습니다.")
        command = build_approved_action_command(proposal, robot, zones)
        with self._lock:
            robot_id = proposal.robot_id
            if robot_id not in self._subscriptions:
                self._subscriptions[robot_id] = self._node.create_subscription(
                    self._string_type,
                    result_topic(robot_id),
                    self._on_result,
                    self._qos,
                )
            self._proposal_robots[proposal.proposal_id] = robot_id
            message = self._string_type()
            message.data = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
            self._action_publisher.publish(message)
        return command

    def publish_estop(self, active: bool, triggered_by: str = "RULE_ENGINE") -> dict[str, Any]:
        if self._node is None:
            raise DispatchError("ROS 2 브리지가 시작되지 않았습니다.")
        command = build_estop_command(active, triggered_by)
        message = self._string_type()
        message.data = json.dumps(command, separators=(",", ":"))
        with self._lock:
            self._estop_publisher.publish(message)
        return command

    def _publish_heartbeat(self) -> None:
        message = self._string_type()
        message.data = json.dumps(
            {"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
            separators=(",", ":"),
        )
        self._heartbeat_publisher.publish(message)

    def _on_result(self, message: Any) -> None:
        try:
            event = json.loads(message.data)
            if event.get("kind") in {"HOLD", "ESTOP"}:
                if self.safety_event_handler is not None:
                    self.safety_event_handler(event)
                return
            if event.get("kind") != "RESULT":
                return
            proposal_id = event.get("proposal_id")
            expected_robot_id = self._proposal_robots.get(proposal_id)
            if expected_robot_id is None or event.get("robot_id") != expected_robot_id:
                LOGGER.warning("대상 Robot이 일치하지 않는 CTL RESULT를 무시했습니다: %s", event)
                return
            report = result_event_to_report(event)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            LOGGER.warning("CTL RESULT 계약 오류: %s", exc)
            return

        threading.Thread(
            target=self._post_result,
            args=(proposal_id, report),
            name=f"ctl-result-{proposal_id}",
            daemon=True,
        ).start()

    def _post_result(self, proposal_id: str, report: AdapterResultReport) -> None:
        url = f"{self.result_api_base}/action-proposals/{proposal_id}/result"
        payload = report.model_dump_json(exclude_none=True).encode("utf-8")
        for attempt in range(3):
            http_request = request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with request.urlopen(http_request, timeout=2) as response:
                    if response.status == 200:
                        return
            except error.HTTPError as exc:
                if exc.code < 500:
                    LOGGER.warning("CTL RESULT 반영이 거부되었습니다(%s): %s", exc.code, proposal_id)
                    return
            except error.URLError:
                pass
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
        LOGGER.error("CTL RESULT 반영에 실패했습니다: %s", proposal_id)


def create_action_dispatcher() -> ActionDispatcher:
    enabled = os.getenv("BACKEND_CTL_ROS_ENABLED", "").lower() in {"1", "true", "yes"}
    if not enabled:
        return MockActionDispatcher()
    return Ros2ActionDispatcher(
        os.getenv("BACKEND_CTL_RESULT_API", "http://127.0.0.1:8000")
    )
