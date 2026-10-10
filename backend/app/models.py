from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


class OntologyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Vector3(OntologyModel):
    x: float
    y: float
    z: float


class Quaternion(OntologyModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


class Pose(OntologyModel):
    frame_id: str
    position: Vector3
    orientation: Quaternion = Field(default_factory=Quaternion)


class MotionLimits(OntologyModel):
    max_speed: float = Field(gt=0)
    max_acceleration: float = Field(gt=0)


class RobotType(str, Enum):
    HUMANOID = "HUMANOID"
    QUADRUPED = "QUADRUPED"
    MANIPULATOR = "MANIPULATOR"
    MOBILE = "MOBILE"


class RobotStatus(str, Enum):
    IDLE = "IDLE"
    MOVING = "MOVING"
    HOLD = "HOLD"
    ESTOP = "ESTOP"
    ERROR = "ERROR"


class Robot(OntologyModel):
    robot_id: str
    robot_type: RobotType
    control_adapter: str
    status: RobotStatus
    pose: Pose
    velocity: Vector3
    motion_limits: MotionLimits
    updated_at: datetime = Field(default_factory=utc_now)


class SensorType(str, Enum):
    ROBOT_CAMERA = "ROBOT_CAMERA"
    CCTV = "CCTV"
    DRONE_CAMERA = "DRONE_CAMERA"


class SensorStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class Sensor(OntologyModel):
    sensor_id: str
    sensor_type: SensorType
    source_uri: str
    frame_id: str
    pose: Pose
    status: SensorStatus
    mounted_robot_id: str | None


class DetectedType(str, Enum):
    WORKER = "WORKER"
    ROBOT = "ROBOT"
    OBJECT = "OBJECT"
    FIRE = "FIRE"
    SMOKE = "SMOKE"
    OBSTACLE = "OBSTACLE"


class Observation(OntologyModel):
    observation_id: str
    sensor_id: str
    timestamp: datetime
    detected_type: DetectedType
    position: Vector3
    frame_id: str
    confidence: float = Field(ge=0, le=1)
    track_id: str | None


class WorkerSafetyStatus(str, Enum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    DANGER = "DANGER"
    UNKNOWN = "UNKNOWN"


class Worker(OntologyModel):
    worker_id: str
    pose: Pose
    current_zone_id: str
    safety_status: WorkerSafetyStatus
    last_seen_at: datetime
    confidence: float = Field(ge=0, le=1)


class PhysicalObject(OntologyModel):
    object_id: str
    object_type: str
    pose: Pose
    current_zone_id: str
    movable: bool
    updated_at: datetime = Field(default_factory=utc_now)


class ZoneType(str, Enum):
    WORK = "WORK"
    KEEP_OUT = "KEEP_OUT"
    SAFE = "SAFE"
    TRANSIT = "TRANSIT"


class Zone(OntologyModel):
    zone_id: str
    zone_type: ZoneType
    geometry: dict[str, Any]
    frame_id: str
    minimum_safety_distance: float = Field(ge=0)


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    HELD = "HELD"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskOrder(OntologyModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    instruction: str = Field(min_length=1)
    target_robot_id: str
    target_object_id: str | None
    destination: dict[str, Any]
    status: TaskStatus = TaskStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)


class NaturalLanguageTaskRequest(OntologyModel):
    instruction: str = Field(min_length=1, examples=["robot-01 로봇을 A구역으로 이동해"])
    target_robot_id: str | None = None
    frame_id: str = "map"


class ActionType(str, Enum):
    MOVE_TO = "MOVE_TO"
    MANIPULATE = "MANIPULATE"
    STOP = "STOP"
    RESUME = "RESUME"


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    HELD = "HELD"


class ActionProposal(OntologyModel):
    proposal_id: str = Field(default_factory=lambda: new_id("proposal"))
    task_id: str
    robot_id: str
    action_type: ActionType
    parameters: dict[str, Any]
    trajectory: list[dict[str, Any]] | None
    frame_id: str
    speed_limit: float = Field(gt=0)
    model_name: str
    model_version: str
    status: ProposalStatus = ProposalStatus.PROPOSED
    proposed_at: datetime = Field(default_factory=utc_now)


class RuleType(str, Enum):
    SPEED = "SPEED"
    COLLISION = "COLLISION"
    KEEP_OUT = "KEEP_OUT"
    SAFETY_DISTANCE = "SAFETY_DISTANCE"
    HAZARD_STATE = "HAZARD_STATE"


class RuleSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class SafetyRule(OntologyModel):
    rule_id: str
    rule_type: RuleType
    target_robot_type: RobotType | str
    condition: dict[str, Any]
    severity: RuleSeverity
    enabled: bool = True


class HazardType(str, Enum):
    WORKER_INTRUSION = "WORKER_INTRUSION"
    COLLISION_RISK = "COLLISION_RISK"
    FIRE = "FIRE"
    SMOKE = "SMOKE"
    FALLING_OBJECT = "FALLING_OBJECT"
    UNKNOWN = "UNKNOWN"


class HazardSeverity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class HazardStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class HazardEvent(OntologyModel):
    hazard_id: str = Field(default_factory=lambda: new_id("hazard"))
    hazard_type: HazardType
    severity: HazardSeverity
    source_observation_ids: list[str]
    related_robot_ids: list[str]
    related_worker_ids: list[str]
    zone_id: str
    status: HazardStatus = HazardStatus.OPEN
    confidence: float = Field(ge=0, le=1)
    detected_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESTOP = "ESTOP"
    EXECUTE = "EXECUTE"
    COMPLETE = "COMPLETE"
    FAIL = "FAIL"


class RuleResult(OntologyModel):
    rule_id: str
    passed: bool
    reason: str


class ExecutionLog(OntologyModel):
    log_id: str = Field(default_factory=lambda: new_id("log"))
    proposal_id: str
    decision: Decision
    rule_results: list[RuleResult]
    hazard_ids: list[str]
    robot_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    result_message: str
