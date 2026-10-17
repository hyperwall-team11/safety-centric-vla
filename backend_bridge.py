"""Bridge for backend API v0.2 objects. Does not patch or impersonate its routes.

The backend supplies actual objects and an optional trusted latest scene snapshot.
No HTTP GET /hazards or /sensors endpoints are assumed to exist.
"""
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field
from .base_models import OntologyModel, ExecutionLog
from .contracts import (EvaluationRequest, EvaluationResponse, SceneContext,
                        SensorState, ZoneState, AdapterState)
from .base_models import Worker
from .backend_client import request_evaluation, SafetyServiceUnavailable


class BackendSceneState(OntologyModel):
    # Optional enrichment, separate from current POST /robots and POST /hazards payloads.
    sensors: list[SensorState] = Field(default_factory=list)
    zones: list[ZoneState] = Field(default_factory=list)
    workers: list[Worker] = Field(default_factory=list)
    adapters: list[AdapterState] = Field(default_factory=list)
    context: SceneContext
    # Keyed by hazard ID. v0.2 hazards have no such fields; never fabricate administrator proof.
    hazard_metadata: dict[str, dict] = Field(default_factory=dict)


class BackendAssessment(OntologyModel):
    phase: Literal['evaluate','execute']
    proposal_status: Literal['APPROVED','REJECTED','HELD']
    can_execute: bool
    requires_estop: bool
    http_status: int
    execution_log: ExecutionLog
    evaluation: EvaluationResponse


def as_dict(obj):
    # Accept backend Pydantic models, including classes from a different package.
    if hasattr(obj, 'model_dump'): return obj.model_dump(mode='json')
    if isinstance(obj, dict): return dict(obj)
    raise ValueError('Expected a backend model or JSON object')


def build_backend_snapshot(*, proposal, robot, task, hazards, scene=None):
    """Build /evaluate request from backend-owned values without changing them.

    scene must represent an actual captured state. Creating a fresh context timestamp
    here with missing scene information does not assert freshness/completeness.
    """
    scene = BackendSceneState.model_validate(scene) if isinstance(scene,dict) else scene
    if scene is not None and not isinstance(scene,BackendSceneState):
        raise ValueError('scene must be BackendSceneState or JSON dict')
    if scene is None:
        scene = BackendSceneState(context=SceneContext(snapshot_at=datetime.now(timezone.utc)))
    hazard_rows = []
    for h in hazards:
        row = as_dict(h)
        extra = scene.hazard_metadata.get(row['hazard_id'],{})
        allowed = {'last_observed_at','resolved_by'}
        if set(extra)-allowed: raise ValueError('Unsupported hazard metadata')
        # Metadata cannot override status, related_robot_ids, zone_id, etc.
        hazard_rows.append(row | extra)
    payload = dict(proposal=as_dict(proposal),robots=[as_dict(robot)] if robot is not None else [],
        tasks=[as_dict(task)] if task is not None else [],hazards=hazard_rows,
        sensors=scene.sensors,zones=scene.zones,workers=scene.workers,adapters=scene.adapters,
        context=scene.context)
    return EvaluationRequest.model_validate(payload).model_dump(mode='json')


def map_backend_result(result: EvaluationResponse, phase: Literal['evaluate','execute']):
    """Preserve v0.2 state and log enums; no speculative EXECUTE log."""
    if phase not in ('evaluate','execute'): raise ValueError('Invalid phase')
    if phase == 'evaluate':
        status = 'APPROVED' if result.can_execute else 'REJECTED'
        decision = 'APPROVE' if result.can_execute else 'REJECT'
        http_status = 200
    else:
        # On success remain APPROVED until caller really dispatches the adapter.
        status = 'APPROVED' if result.can_execute else 'HELD'
        decision = 'APPROVE' if result.can_execute else 'ESTOP'
        http_status = 200 if result.can_execute else 409
    data = result.execution_log.model_dump(mode='json')
    data.update(decision=decision,
        result_message=f'{result.decision}; phase={phase}; requires_estop={result.requires_estop}; '
        '판정 기록이며 제어 신호 발행/정지 완료 기록이 아님')
    return BackendAssessment(phase=phase,proposal_status=status,can_execute=result.can_execute,
        requires_estop=result.requires_estop,http_status=http_status,
        execution_log=ExecutionLog.model_validate(data),evaluation=result)


def assess_for_backend(*, proposal, robot, task, hazards, scene=None,
                       phase: Literal['evaluate','execute']='evaluate',
                       url='http://127.0.0.1:8001/evaluate'):
    """Call from existing evaluate/execute route. Raises on every transport/input failure.

    Caller must deny execution, set REJECTED/HELD, persist failure, and return 503 on
    SafetyServiceUnavailable. Do not fall back to the original weaker four checks.
    """
    try:
        if phase not in ('evaluate','execute'): raise ValueError('Invalid phase')
        expected_status = 'PROPOSED' if phase=='evaluate' else 'APPROVED'
        if as_dict(proposal).get('status') != expected_status:
            raise ValueError(f'{phase} requires proposal status {expected_status}')
        payload = build_backend_snapshot(proposal=proposal,robot=robot,task=task,hazards=hazards,scene=scene)
        result = request_evaluation(payload,url=url)
        return map_backend_result(result,phase)
    except (ValueError,TypeError,KeyError) as exc:
        raise SafetyServiceUnavailable('백엔드 스냅샷 구성 실패: 실행 차단 필요') from exc
