from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
NaturalLanguageInstruction = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


class OntologyModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        allow_inf_nan=False,
    )


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
    last_heartbeat_at: datetime | None = None
    battery_level: float | None = Field(default=None, ge=0, le=1)


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
    covered_zone_ids: list[str] = Field(default_factory=list)
    last_frame_at: datetime | None = None
    calibrated: bool | None = None


class DetectedType(str, Enum):
    WORKER = "WORKER"
    ROBOT = "ROBOT"
    OBJECT = "OBJECT"
    FIRE = "FIRE"
    SMOKE = "SMOKE"
    OBSTACLE = "OBSTACLE"


class PositionSource(str, Enum):
    WORLD = "WORLD"
    ZONE_MAPPED = "ZONE_MAPPED"
    IMAGE_ONLY = "IMAGE_ONLY"


class Observation(OntologyModel):
    observation_id: str
    sensor_id: str
    timestamp: datetime
    detected_type: DetectedType
    position: Vector3
    frame_id: str
    confidence: float = Field(ge=0, le=1)
    track_id: str | None
    ingested_at: datetime | None = None
    bbox: tuple[float, float, float, float] | None = None
    position_source: PositionSource | None = None


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
    source_sensor_ids: list[str] = Field(default_factory=list)


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
    max_speed: float | None = Field(default=None, gt=0)
    estop_distance: float = Field(default=0.6, ge=0)


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    HELD = "HELD"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CoordinateDestination(OntologyModel):
    type: Literal["COORDINATE"] = "COORDINATE"
    frame_id: NonBlankString
    position: Vector3


class ZoneDestination(OntologyModel):
    type: Literal["ZONE"] = "ZONE"
    zone_id: NonBlankString


TaskDestination = Annotated[
    CoordinateDestination | ZoneDestination,
    Field(discriminator="type"),
]


class TaskOrder(OntologyModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    instruction: NonBlankString
    target_robot_id: NonBlankString
    target_object_id: NonBlankString | None
    destination: TaskDestination
    status: TaskStatus = TaskStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)


class NaturalLanguageTaskRequest(OntologyModel):
    instruction: NaturalLanguageInstruction = Field(
        examples=["robot-01 로봇을 A구역으로 이동해"],
    )
    target_robot_id: NonBlankString | None = None
    frame_id: NonBlankString = "map"


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


class ManipulationType(str, Enum):
    PICK = "PICK"
    PLACE = "PLACE"


class ActionPriority(str, Enum):
    NORMAL = "NORMAL"
    SAFETY = "SAFETY"


class MoveToParameters(OntologyModel):
    target_pose: TaskDestination
    speed_limit: float = Field(gt=0)
    priority: ActionPriority | None = None


class ManipulateParameters(OntologyModel):
    manipulation: ManipulationType
    object_id: NonBlankString
    target_pose: TaskDestination
    priority: ActionPriority | None = None


class StopParameters(OntologyModel):
    priority: ActionPriority | None = None


class ResumeParameters(OntologyModel):
    task_id: NonBlankString
    priority: ActionPriority | None = None


class ActionProposal(OntologyModel):
    proposal_id: str = Field(default_factory=lambda: new_id("proposal"))
    task_id: NonBlankString
    robot_id: NonBlankString
    action_type: ActionType
    parameters: dict[str, Any]
    trajectory: list[dict[str, Any]] | None
    frame_id: NonBlankString
    speed_limit: float = Field(gt=0)
    model_name: NonBlankString
    model_version: NonBlankString
    status: ProposalStatus = ProposalStatus.PROPOSED
    proposed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_action_parameters(self) -> ActionProposal:
        action_type = ActionType(self.action_type)
        parameter_models = {
            ActionType.MOVE_TO: MoveToParameters,
            ActionType.MANIPULATE: ManipulateParameters,
            ActionType.STOP: StopParameters,
            ActionType.RESUME: ResumeParameters,
        }
        validated = parameter_models[action_type].model_validate(self.parameters)
        if (
            validated.priority == ActionPriority.SAFETY
            and action_type not in {ActionType.STOP, ActionType.RESUME}
        ):
            raise ValueError(
                "SAFETY priority는 시스템이 발행하는 STOP 또는 RESUME에만 사용할 수 있습니다."
            )
        if (
            action_type == ActionType.MOVE_TO
            and validated.speed_limit != self.speed_limit
        ):
            raise ValueError(
                "MOVE_TO parameters.speed_limit는 ActionProposal.speed_limit와 같아야 합니다."
            )
        self.parameters = validated.model_dump(mode="json", exclude_none=True)
        return self


class ProposalGenerationRequest(OntologyModel):
    speed_limit: float | None = Field(default=None, gt=0)
    manipulation: ManipulationType | None = None


class RuleType(str, Enum):
    SPEED = "SPEED"
    COLLISION = "COLLISION"
    KEEP_OUT = "KEEP_OUT"
    SAFETY_DISTANCE = "SAFETY_DISTANCE"
    HAZARD_STATE = "HAZARD_STATE"
    OBSERVATION_GAP = "OBSERVATION_GAP"


class RuleSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ViolationAction(str, Enum):
    REJECT = "REJECT"
    ESTOP = "ESTOP"


class SafetyRule(OntologyModel):
    rule_id: str
    rule_type: RuleType
    target_robot_type: RobotType | str
    condition: dict[str, Any]
    severity: RuleSeverity
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)
    on_violation: ViolationAction | None = None

    @model_validator(mode="after")
    def set_default_violation_action(self) -> SafetyRule:
        if self.on_violation is None:
            self.on_violation = (
                ViolationAction.ESTOP
                if self.severity == RuleSeverity.CRITICAL
                else ViolationAction.REJECT
            )
        return self


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


class HazardResolvedBy(str, Enum):
    SENSOR_AUTO = "SENSOR_AUTO"
    OPERATOR = "OPERATOR"


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
    last_observed_at: datetime | None = None
    resolved_by: HazardResolvedBy | None = None


class HazardResolveRequest(OntologyModel):
    resolved_by: HazardResolvedBy


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESTOP = "ESTOP"
    EXECUTE = "EXECUTE"
    COMPLETE = "COMPLETE"
    FAIL = "FAIL"


class TriggerSource(str, Enum):
    RULE_ENGINE = "RULE_ENGINE"
    OPERATOR = "OPERATOR"
    HEARTBEAT_LOSS = "HEARTBEAT_LOSS"


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
    evaluated_in_ms: float | None = Field(default=None, ge=0)
    triggered_by: TriggerSource | None = None
