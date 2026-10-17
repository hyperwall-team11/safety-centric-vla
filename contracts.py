from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from .base_models import (
    OntologyModel, Robot, Sensor, Worker, Zone, HazardEvent,
    ActionProposal, TaskOrder, ExecutionLog,
)


class SensorState(Sensor):
    covered_zone_ids: list[str] = Field(default_factory=list)
    last_frame_at: datetime | None = None
    # Capture timestamp of the latest successfully processed frame (not processing completion time).
    last_processed_at: datetime | None = None
    calibrated: bool = False


class ZoneState(Zone):
    max_speed: float | None = Field(default=None, gt=0)
    estop_distance: float = Field(default=0.6, ge=0)


class HazardState(HazardEvent):
    last_observed_at: datetime | None = None
    resolved_by: Literal['SENSOR_AUTO', 'OPERATOR'] | None = None


class AdapterState(OntologyModel):
    adapter_id: str
    robot_type: str
    ready: bool
    supported_actions: list[str]
    last_heartbeat_at: datetime


class SceneContext(OntologyModel):
    snapshot_at: datetime
    # Explicit completeness assertions made by the trusted backend, never by VLA.
    hazards_complete: bool = False
    workers_complete: bool = False
    zones_complete: bool = False
    robot_radius_m: float | None = Field(default=None, gt=0)
    # Backend determines whether HOLD is actually replan-capable.
    hold_replan_allowed: bool = False


class EvaluationRequest(OntologyModel):
    proposal: ActionProposal
    robots: list[Robot] = Field(default_factory=list)
    tasks: list[TaskOrder] = Field(default_factory=list)
    hazards: list[HazardState] = Field(default_factory=list)
    sensors: list[SensorState] = Field(default_factory=list)
    zones: list[ZoneState] = Field(default_factory=list)
    workers: list[Worker] = Field(default_factory=list)
    adapters: list[AdapterState] = Field(default_factory=list)
    context: SceneContext

    @model_validator(mode='after')
    def unique_ids_and_utc(self):
        for field, key in [('robots','robot_id'), ('tasks','task_id'),
                           ('hazards','hazard_id'), ('sensors','sensor_id'),
                           ('zones','zone_id'), ('workers','worker_id'),
                           ('adapters','adapter_id')]:
            ids = [getattr(x, key) for x in getattr(self, field)]
            if any(not x.strip() for x in ids) or len(ids) != len(set(ids)):
                raise ValueError(f'{field}: IDs must be nonblank and unique')
        def visit(x):
            if isinstance(x, datetime):
                if x.utcoffset() is None or x.utcoffset().total_seconds() != 0:
                    raise ValueError('All timestamps must be timezone-aware UTC')
            elif isinstance(x, dict):
                for v in x.values(): visit(v)
            elif isinstance(x, list):
                for v in x: visit(v)
        visit(self.model_dump())
        return self


class CheckResult(OntologyModel):
    rule_id: str
    status: Literal['PASS', 'FAIL', 'UNKNOWN']
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvaluationResponse(OntologyModel):
    evaluation_id: str
    proposal_id: str
    decision: Literal['APPROVE', 'REJECT', 'INDETERMINATE']
    can_execute: bool
    requires_estop: bool
    rule_results: list[CheckResult]
    hazard_ids: list[str]
    affected_zone_ids: list[str]
    evaluated_at: datetime
    evaluated_in_ms: float
    policy_version: str
    input_sha256: str
    execution_log: ExecutionLog
