"""모듈 간 경계 합의용 토픽·QoS·코드 (개발계획서 v1.0 부록 A, 스키마 v1.1 7·9장).

메시지 본문은 OpenAPI 확정 전까지 std_msgs/String에 JSON으로 담는다.
"""
import re

from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

APPROVED_ACTION_TOPIC = "/safety/approved_action"
ESTOP_TOPIC = "/safety/estop"
HEARTBEAT_TOPIC = "/safety/heartbeat"


def topic_segment(robot_id: str) -> str:
    """ROS 2 토픽 이름은 영숫자와 '_'만 허용하므로 'robot-01' → 'robot_01'로 바꾼다."""
    segment = re.sub(r"[^A-Za-z0-9_]", "_", robot_id)
    return f"r_{segment}" if segment[:1].isdigit() else segment


def result_topic(robot_id: str) -> str:
    return f"/adapter/{topic_segment(robot_id)}/result"


def state_topic(robot_id: str) -> str:
    # 부록 A에 없는 제안 토픽: Robot 객체 속성(status, pose, velocity) 갱신용
    return f"/adapter/{topic_segment(robot_id)}/state"


RELIABLE = QoSProfile(
    history=HistoryPolicy.KEEP_LAST, depth=10, reliability=ReliabilityPolicy.RELIABLE
)
# E-Stop: latched(TRANSIENT_LOCAL) + Reliable. 늦게 뜬 어댑터도 마지막 정지 상태를 받는다.
ESTOP_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
HEARTBEAT_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE
)
