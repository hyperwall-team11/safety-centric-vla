from __future__ import annotations

import asyncio
import logging
import threading
from hashlib import sha256
from math import dist
from os import getenv
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel

from .action_planner import ActionPlanningError, RuleBasedActionPlanner
from .ctl_bridge import (
    CommandSerializationError,
    DispatchError,
    create_action_dispatcher,
)
from .models import (
    ActionProposal,
    ActionType,
    AdapterFailureCode,
    AdapterResultReport,
    AdapterResultStatus,
    Decision,
    ExecutionLog,
    HazardEvent,
    HazardResolveRequest,
    HazardResolvedBy,
    HazardSeverity,
    HazardStatus,
    NaturalLanguageTaskRequest,
    ProposalGenerationRequest,
    ProposalStatus,
    RequestValidationErrorResponse,
    Robot,
    RobotStatus,
    RuleResult,
    SafetyRule,
    Sensor,
    TaskOrder,
    TaskOrderPreview,
    TaskOrderPreviewBatchItem,
    TaskOrderPreviewBatchRequest,
    TaskOrderPreviewBatchResponse,
    TaskParseErrorDetail,
    TaskParseErrorResponse,
    TaskStatus,
    TriggerSource,
    Worker,
    Zone,
    utc_now,
)
from .safety import WORKER_SNAPSHOT_MAX_AGE_SECONDS, default_rules, evaluate_proposal
from .state_stream import state_broadcaster
from .store import store
from .task_parser import TaskParseError, parse_task_order, parse_task_order_preview


app = FastAPI(
    title="Safety-Centric VLA Ontology API",
    version="0.2.0",
    description="TaskOrder 변환과 최소 Action 안전 검증을 제공하는 MVP API",
)
CONSOLE_DIR = Path(__file__).resolve().parent / "static" / "console"
app.mount(
    "/console/assets",
    StaticFiles(directory=CONSOLE_DIR),
    name="console-assets",
)
planner = RuleBasedActionPlanner()
ACTIVE_PROPOSAL_STATUSES = {
    ProposalStatus.PROPOSED,
    ProposalStatus.APPROVED,
    ProposalStatus.EXECUTING,
    ProposalStatus.HELD,
}
PLANNABLE_TASK_STATUSES = {
    TaskStatus.CREATED,
    TaskStatus.PLANNING,
}
TASK_LIST_RESPONSE_HEADERS = {
    "X-Total-Count": {
        "description": "필터 적용 후 페이지네이션 전 전체 TaskOrder 수",
        "schema": {"type": "integer", "minimum": 0},
    },
}
TASK_ORDER_RESPONSE_HEADERS = {
    "Location": {
        "description": "생성되거나 재전송된 TaskOrder의 조회 경로",
        "schema": {"type": "string"},
    },
    "Idempotency-Replayed": {
        "description": "멱등성 키 요청의 기존 TaskOrder 재전송 여부",
        "schema": {"type": "string", "enum": ["true", "false"]},
    },
}
# control/robot_adapter/diff_drive_core.py의 도착 판정 허용 오차와 동일하다.
TRAJECTORY_START_TOLERANCE_M = 0.05
STATE_HEARTBEAT_SECONDS = 15.0
# UI 상태 스트림 부하 제한이며 ROS 2 안전 heartbeat·판정 주기와 무관하다.
STATE_MIN_PUSH_INTERVAL_SECONDS = 0.2
STATE_EXECUTION_LOG_LIMIT = 100
NON_MUTATING_POST_PATHS = {
    "/task-orders/preview",
    "/task-orders/preview/batch",
}


class StateSnapshot(BaseModel):
    type: Literal["STATE_SNAPSHOT"] = "STATE_SNAPSHOT"
    revision: int
    observed_at: AwareDatetime
    workers_observed_at: AwareDatetime | None
    robots: list[Robot]
    sensors: list[Sensor]
    workers: list[Worker] | None
    zones: list[Zone]
    task_orders: list[TaskOrder]
    action_proposals: list[ActionProposal]
    hazards: list[HazardEvent]
    safety_rules: list[SafetyRule]
    execution_logs: list[ExecutionLog]


def make_state_snapshot() -> dict:
    """Copy a consistent store view quickly, then validate and serialize unlocked."""
    with store.lock:
        snapshot_values = {
            "revision": state_broadcaster.revision,
            "observed_at": utc_now(),
            "workers_observed_at": store.workers_observed_at,
            "robots": list(store.robots.values()),
            "sensors": list(store.sensors.values()),
            "workers": None if store.workers is None else list(store.workers),
            "zones": list(store.zones.values()),
            "task_orders": list(store.tasks.values()),
            "action_proposals": list(store.proposals.values()),
            "hazards": list(store.hazards.values()),
            "safety_rules": list(store.rules.values()),
            "execution_logs": list(store.logs[-STATE_EXECUTION_LOG_LIMIT:]),
        }
    snapshot = StateSnapshot(**snapshot_values)
    return snapshot.model_dump(mode="json")


@app.middleware("http")
async def publish_successful_state_writes(request: Request, call_next):
    response = await call_next(request)
    is_state_write = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    if (
        is_state_write
        and request.url.path not in NON_MUTATING_POST_PATHS
        and 200 <= response.status_code < 400
    ):
        state_broadcaster.publish()
    return response


@app.websocket("/ws/state")
async def stream_state(websocket: WebSocket) -> None:
    await websocket.accept()
    subscription = state_broadcaster.subscribe()
    try:
        loop = asyncio.get_running_loop()
        if not await send_state_snapshot(websocket):
            return
        last_sent_at = loop.time()
        while True:
            try:
                await asyncio.wait_for(
                    subscription.event.wait(),
                    timeout=STATE_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
            remaining_interval = (
                STATE_MIN_PUSH_INTERVAL_SECONDS - (loop.time() - last_sent_at)
            )
            if remaining_interval > 0:
                await asyncio.sleep(remaining_interval)
            # Delay 중 발생한 변경도 이어지는 전체 스냅샷 하나로 합친다.
            subscription.event.clear()
            if not await send_state_snapshot(websocket):
                return
            last_sent_at = loop.time()
    except WebSocketDisconnect:
        pass
    finally:
        state_broadcaster.unsubscribe(subscription.subscription_id)


async def send_state_snapshot(websocket: WebSocket) -> bool:
    snapshot = await asyncio.to_thread(make_state_snapshot)
    try:
        await websocket.send_json(snapshot)
    except (WebSocketDisconnect, RuntimeError):
        # send_json과 연결 종료가 경합하면 Starlette가 RuntimeError를 낼 수 있다.
        return False
    return True


def normalize_idempotency_key(idempotency_key: str | None) -> str | None:
    normalized_key = idempotency_key.strip() if idempotency_key is not None else None
    if idempotency_key is not None and not normalized_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key는 비어 있을 수 없습니다.")
    if normalized_key is not None and len(normalized_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key는 128자 이하여야 합니다.")
    return normalized_key


def make_request_fingerprint(request: BaseModel) -> str:
    canonical_request = request.model_dump_json(exclude_none=False)
    return sha256(canonical_request.encode("utf-8")).hexdigest()


def trajectory_start_distance_m(robot: Robot, trajectory_start: dict) -> float | None:
    position = trajectory_start.get("position")
    if not isinstance(position, dict):
        return None
    try:
        return dist(
            (robot.pose.position.x, robot.pose.position.y, robot.pose.position.z),
            (float(position["x"]), float(position["y"]), float(position["z"])),
        )
    except (KeyError, TypeError, ValueError):
        return None


def seed_rules() -> None:
    if not store.rules:
        rules = default_rules(perception_enabled=getenv("BACKEND_PERCEPTION_SAFETY_ENABLED") == "1")
        store.rules.update({rule.rule_id: rule for rule in rules})


seed_rules()


def apply_ctl_safety_event(event: dict) -> None:
    """CTL의 HOLD·ESTOP을 더 낮은 안전 상태로 되돌리지 않고 반영한다."""
    kind = event.get("kind")
    robot_id = event.get("robot_id")
    if kind not in {"HOLD", "ESTOP"} or not isinstance(robot_id, str):
        return
    changed = False
    with store.lock:
        robot = store.robots.get(robot_id)
        if robot is None:
            return
        proposal_id = event.get("proposal_id")
        proposals = [
            proposal
            for proposal in store.proposals.values()
            if proposal.robot_id == robot_id
            and proposal.status == ProposalStatus.EXECUTING
            and (proposal_id is None or proposal.proposal_id == proposal_id)
        ]
        robot.status = RobotStatus.ESTOP if kind == "ESTOP" else RobotStatus.HOLD
        robot.updated_at = utc_now()
        changed = True
        trigger_value = event.get("triggered_by", "RULE_ENGINE")
        try:
            triggered_by = TriggerSource(trigger_value)
        except ValueError:
            triggered_by = TriggerSource.RULE_ENGINE
        for proposal in proposals:
            proposal.status = ProposalStatus.HELD
            task = store.tasks.get(proposal.task_id)
            if task is not None:
                task.status = TaskStatus.HELD
            store.logs.append(
                ExecutionLog(
                    proposal_id=proposal.proposal_id,
                    decision=Decision.ESTOP,
                    rule_results=[],
                    hazard_ids=[],
                    robot_id=robot_id,
                    result_message=f"CTL {kind} 이벤트 수신",
                    triggered_by=triggered_by,
                )
            )
    if changed:
        state_broadcaster.publish()


LOGGER = logging.getLogger(__name__)


def robots_to_stop(hazard: HazardEvent) -> list[str]:
    """CRITICAL·OPEN HazardEvent와 연결된 로봇 중 승인 행동을 실행 중인 로봇. store.lock 안에서 호출한다."""
    if hazard.severity != HazardSeverity.CRITICAL or hazard.status != HazardStatus.OPEN:
        return []
    # 기존 HAZARD_STATE 판정과 같은 연결 기준: related_robot_ids 또는 목적지 Zone 일치
    related = set(hazard.related_robot_ids)
    stop: set[str] = set()
    for proposal in store.proposals.values():
        if proposal.status != ProposalStatus.EXECUTING:
            continue
        task = store.tasks.get(proposal.task_id)
        destination_zone_id = getattr(task.destination, "zone_id", None) if task else None
        if proposal.robot_id in related or (
            destination_zone_id is not None and destination_zone_id == hazard.zone_id
        ):
            stop.add(proposal.robot_id)
    return sorted(stop)


ESTOP_PUBLISH_LOCK = threading.Lock()


def sync_estop(reason: str) -> None:
    """저장소의 현재 정지 상태(estop_hazard_ids 유무)를 발행한다.

    store.lock 밖에서 호출해 위험 등록·결과 반영을 막지 않는다. 발행은 ESTOP_PUBLISH_LOCK으로
    직렬화하고 발행 직전에 상태를 다시 읽으므로, 등록·해제가 동시에 일어나도 마지막 발행은
    항상 마지막 저장소 상태와 같다(Transient Local·Keep Last 1 latched 값 보장).
    """
    with ESTOP_PUBLISH_LOCK:
        with store.lock:
            active = bool(store.estop_hazard_ids)
        _publish_estop(active, reason)


def _publish_estop(active: bool, reason: str) -> None:
    publisher = getattr(action_dispatcher, "publish_estop", None)
    if publisher is None:
        LOGGER.error("E-Stop을 발행할 수 없는 전송기입니다(%s): %s", action_dispatcher.name, reason)
        return
    try:
        publisher(active, "RULE_ENGINE")
        LOGGER.warning("E-Stop %s 발행: %s", "정지" if active else "해제", reason)
    except DispatchError as exc:
        LOGGER.error("E-Stop 발행 실패(%s): %s", reason, exc)


action_dispatcher = create_action_dispatcher()
if hasattr(action_dispatcher, "safety_event_handler"):
    action_dispatcher.safety_event_handler = apply_ctl_safety_event


@app.on_event("startup")
def start_action_dispatcher() -> None:
    action_dispatcher.start()


@app.on_event("shutdown")
def stop_action_dispatcher() -> None:
    action_dispatcher.stop()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/console", include_in_schema=False)
def operator_console() -> FileResponse:
    return FileResponse(CONSOLE_DIR / "index.html")


@app.post("/robots", response_model=Robot, status_code=status.HTTP_201_CREATED)
def create_robot(robot: Robot) -> Robot:
    with store.lock:
        if robot.robot_id in store.robots:
            raise HTTPException(status_code=409, detail="이미 등록된 robot_id입니다.")
        store.robots[robot.robot_id] = robot
    return robot


@app.get("/robots", response_model=list[Robot])
def list_robots() -> list[Robot]:
    return list(store.robots.values())


@app.post("/zones", response_model=Zone, status_code=status.HTTP_201_CREATED)
def create_zone(zone: Zone) -> Zone:
    with store.lock:
        if zone.zone_id in store.zones:
            raise HTTPException(status_code=409, detail="이미 등록된 zone_id입니다.")
        store.zones[zone.zone_id] = zone
    return zone


@app.get("/zones", response_model=list[Zone])
def list_zones() -> list[Zone]:
    return list(store.zones.values())


@app.get(
    "/task-orders",
    response_model=list[TaskOrder],
    responses={
        status.HTTP_200_OK: {
            "description": "필터 및 페이지네이션된 TaskOrder 목록",
            "headers": TASK_LIST_RESPONSE_HEADERS,
        },
    },
)
def list_tasks(
    response: Response,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    target_robot_id: Annotated[str | None, Query(min_length=1)] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TaskOrder]:
    with store.lock:
        tasks = list(store.tasks.values())
        if task_status is not None:
            tasks = [task for task in tasks if task.status == task_status]
        if target_robot_id is not None:
            tasks = [
                task
                for task in tasks
                if task.target_robot_id == target_robot_id
            ]
        response.headers["X-Total-Count"] = str(len(tasks))
        if order == "desc":
            tasks.reverse()
        return tasks[offset : offset + limit]


@app.post(
    "/task-orders/preview",
    response_model=TaskOrderPreview,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "요청 검증 또는 자연어 작업 해석 실패",
            "model": TaskParseErrorResponse | RequestValidationErrorResponse,
        },
    },
)
def preview_task_from_natural_language(
    request: NaturalLanguageTaskRequest,
) -> TaskOrderPreview:
    with store.lock:
        known_robot_ids = list(store.robots)
    try:
        return parse_task_order_preview(request, known_robot_ids)
    except TaskParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code.value, "message": str(exc)},
        ) from exc


@app.post(
    "/task-orders/preview/batch",
    response_model=TaskOrderPreviewBatchResponse,
)
def preview_tasks_from_natural_language(
    request: TaskOrderPreviewBatchRequest,
) -> TaskOrderPreviewBatchResponse:
    with store.lock:
        known_robot_ids = list(store.robots)

    results: list[TaskOrderPreviewBatchItem] = []
    for index, item in enumerate(request.items):
        try:
            preview = parse_task_order_preview(item, known_robot_ids)
            results.append(
                TaskOrderPreviewBatchItem(
                    index=index,
                    valid=True,
                    preview=preview,
                )
            )
        except TaskParseError as exc:
            results.append(
                TaskOrderPreviewBatchItem(
                    index=index,
                    valid=False,
                    error=TaskParseErrorDetail(
                        code=exc.code.value,
                        message=str(exc),
                    ),
                )
            )

    valid_count = sum(result.valid for result in results)
    return TaskOrderPreviewBatchResponse(
        total=len(results),
        valid_count=valid_count,
        invalid_count=len(results) - valid_count,
        results=results,
    )


@app.post(
    "/task-orders/from-natural-language",
    response_model=TaskOrder,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "description": "동일한 Idempotency-Key 요청의 기존 TaskOrder 재전송",
            "model": TaskOrder,
            "headers": TASK_ORDER_RESPONSE_HEADERS,
        },
        status.HTTP_201_CREATED: {
            "description": "새 TaskOrder 생성",
            "model": TaskOrder,
            "headers": TASK_ORDER_RESPONSE_HEADERS,
        },
        status.HTTP_409_CONFLICT: {
            "description": "동일한 멱등성 키의 요청 본문 충돌",
        },
    },
)
def create_task_from_natural_language(
    request: NaturalLanguageTaskRequest,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> TaskOrder:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = make_request_fingerprint(request)
    with store.lock:
        if normalized_key is not None:
            replay = store.task_idempotency.get(normalized_key)
            if replay is not None:
                task_id, previous_fingerprint = replay
                if previous_fingerprint != request_fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="같은 Idempotency-Key를 다른 요청 본문에 재사용할 수 없습니다.",
                    )
                existing = store.tasks.get(task_id)
                if existing is not None:
                    response.status_code = status.HTTP_200_OK
                    response.headers["Location"] = f"/task-orders/{existing.task_id}"
                    response.headers["Idempotency-Replayed"] = "true"
                    return existing

        try:
            task = parse_task_order(request, list(store.robots))
        except TaskParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.tasks[task.task_id] = task
        if normalized_key is not None:
            store.task_idempotency[normalized_key] = (
                task.task_id,
                request_fingerprint,
            )
            response.headers["Idempotency-Replayed"] = "false"
        response.headers["Location"] = f"/task-orders/{task.task_id}"
    return task


@app.get("/task-orders/{task_id}", response_model=TaskOrder)
def get_task(task_id: str) -> TaskOrder:
    task = store.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TaskOrder를 찾을 수 없습니다.")
    return task


@app.post(
    "/task-orders/{task_id}/action-proposals",
    response_model=ActionProposal,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "description": "동일한 Idempotency-Key 요청의 기존 Proposal 재전송",
            "model": ActionProposal,
        },
        status.HTTP_409_CONFLICT: {
            "description": "작업 상태, 활성 Proposal 또는 멱등성 키 충돌",
        },
    },
)
def generate_action_proposal(
    task_id: str,
    request: ProposalGenerationRequest,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> ActionProposal:
    normalized_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = make_request_fingerprint(request)
    with store.lock:
        task = store.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="TaskOrder를 찾을 수 없습니다.")

        if normalized_key is not None:
            replay = store.proposal_idempotency.get((task_id, normalized_key))
            if replay is not None:
                proposal_id, previous_fingerprint = replay
                if previous_fingerprint != request_fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="같은 Idempotency-Key를 다른 요청 본문에 재사용할 수 없습니다.",
                    )
                existing = store.proposals.get(proposal_id)
                if existing is not None:
                    response.status_code = status.HTTP_200_OK
                    return existing

        if task.status not in PLANNABLE_TASK_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"{task.status} 상태의 TaskOrder에서는 제안을 생성할 수 없습니다.",
            )
        robot = store.robots.get(task.target_robot_id)
        if robot is None:
            raise HTTPException(status_code=404, detail="TaskOrder의 대상 Robot을 찾을 수 없습니다.")
        if robot.status == "ERROR":
            raise HTTPException(status_code=409, detail="ERROR 상태의 로봇에는 제안을 생성할 수 없습니다.")

        active_proposal = next(
            (
                proposal
                for proposal in store.proposals.values()
                if proposal.task_id == task_id
                and proposal.status in ACTIVE_PROPOSAL_STATUSES
            ),
            None,
        )
        if active_proposal is not None:
            raise HTTPException(
                status_code=409,
                detail=f"이 TaskOrder에는 활성 ActionProposal이 이미 있습니다: {active_proposal.proposal_id}",
            )

        try:
            destination_zone = store.zones.get(getattr(task.destination, "zone_id", None))
            proposal = planner.plan(task, robot, request, destination_zone=destination_zone)
        except ActionPlanningError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store.proposals[proposal.proposal_id] = proposal
        if task.status == TaskStatus.CREATED:
            task.status = TaskStatus.PLANNING
        if normalized_key is not None:
            store.proposal_idempotency[(task_id, normalized_key)] = (
                proposal.proposal_id,
                request_fingerprint,
            )
        return proposal


@app.get(
    "/task-orders/{task_id}/action-proposals",
    response_model=list[ActionProposal],
)
def list_task_action_proposals(task_id: str) -> list[ActionProposal]:
    with store.lock:
        if task_id not in store.tasks:
            raise HTTPException(status_code=404, detail="TaskOrder를 찾을 수 없습니다.")
        return [
            proposal
            for proposal in store.proposals.values()
            if proposal.task_id == task_id
        ]


@app.post("/action-proposals", response_model=ActionProposal, status_code=status.HTTP_201_CREATED)
def create_proposal(proposal: ActionProposal) -> ActionProposal:
    with store.lock:
        robot = store.robots.get(proposal.robot_id)
        if robot is None:
            raise HTTPException(status_code=404, detail="대상 Robot을 찾을 수 없습니다.")
        task = store.tasks.get(proposal.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="대상 TaskOrder를 찾을 수 없습니다.")
        if proposal.robot_id != task.target_robot_id:
            raise HTTPException(
                status_code=409,
                detail="ActionProposal의 robot_id가 TaskOrder의 대상 로봇과 다릅니다.",
            )
        if task.status not in PLANNABLE_TASK_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"{task.status} 상태의 TaskOrder에는 제안을 등록할 수 없습니다.",
            )
        if robot.status == "ERROR":
            raise HTTPException(status_code=409, detail="ERROR 상태의 로봇에는 제안을 등록할 수 없습니다.")
        if proposal.proposal_id in store.proposals:
            raise HTTPException(status_code=409, detail="이미 등록된 proposal_id입니다.")
        if any(
            existing.task_id == proposal.task_id
            and existing.status in ACTIVE_PROPOSAL_STATUSES
            for existing in store.proposals.values()
        ):
            raise HTTPException(status_code=409, detail="이 TaskOrder에는 활성 ActionProposal이 이미 있습니다.")
        proposal.status = ProposalStatus.PROPOSED
        store.proposals[proposal.proposal_id] = proposal
        if task.status == TaskStatus.CREATED:
            task.status = TaskStatus.PLANNING
        return proposal


@app.put("/sensors/{sensor_id}", response_model=Sensor)
def update_sensor(sensor_id: str, sensor: Sensor) -> Sensor:
    if sensor_id != sensor.sensor_id:
        raise HTTPException(status_code=422, detail="경로와 본문의 sensor_id가 다릅니다.")
    with store.lock:
        store.sensors[sensor_id] = sensor
    return sensor


class WorkerSnapshot(BaseModel):
    observed_at: AwareDatetime
    workers: list[Worker]


@app.get(
    "/workers/snapshot",
    response_model=WorkerSnapshot,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "저장된 Worker 관측 스냅샷 없음",
        },
    },
)
def get_worker_snapshot() -> WorkerSnapshot:
    with store.lock:
        if store.workers_observed_at is None or store.workers is None:
            raise HTTPException(
                status_code=404,
                detail="Worker 스냅샷을 찾을 수 없습니다.",
            )
        return WorkerSnapshot(
            observed_at=store.workers_observed_at,
            workers=list(store.workers),
        )


@app.put("/workers/snapshot", response_model=WorkerSnapshot)
def replace_worker_snapshot(snapshot: WorkerSnapshot) -> WorkerSnapshot:
    workers = snapshot.workers
    if len({worker.worker_id for worker in workers}) != len(workers):
        raise HTTPException(status_code=422, detail="worker_id가 중복되었습니다.")
    with store.lock:
        if snapshot.observed_at > utc_now():
            raise HTTPException(status_code=422, detail="미래 시각의 Worker 스냅샷입니다.")
        previous_at = store.workers_observed_at
        if previous_at is not None and snapshot.observed_at <= previous_at:
            if snapshot.observed_at == previous_at and workers == store.workers:
                return snapshot
            raise HTTPException(status_code=409, detail="이미 저장된 스냅샷보다 최신이어야 합니다.")
        store.workers = workers
        store.workers_observed_at = snapshot.observed_at
    return snapshot


def evaluate_current_safety(
    proposal: ActionProposal, task: TaskOrder, robot: Robot
) -> tuple[list[RuleResult], list[str], bool]:
    now = utc_now()
    observed_at = store.workers_observed_at
    workers = None
    if observed_at is not None and (
        0 <= (now - observed_at).total_seconds() < WORKER_SNAPSHOT_MAX_AGE_SECONDS
    ):
        workers = store.workers
    return evaluate_proposal(
        proposal,
        task,
        robot,
        list(store.hazards.values()),
        list(store.rules.values()),
        list(store.zones.values()),
        sensors=list(store.sensors.values()),
        workers=workers,
        now=now,
    )


@app.post("/action-proposals/{proposal_id}/evaluate", response_model=ActionProposal)
def evaluate_action(proposal_id: str) -> ActionProposal:
    started_at = perf_counter()
    with store.lock:
        proposal = store.proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")
        if proposal.status != ProposalStatus.PROPOSED:
            raise HTTPException(status_code=409, detail="PROPOSED 상태만 평가할 수 있습니다.")

        robot = store.robots[proposal.robot_id]
        task = store.tasks[proposal.task_id]
        rule_results, hazard_ids, estop_requested = evaluate_current_safety(proposal, task, robot)
        approved = all(result.passed for result in rule_results)
        proposal.status = ProposalStatus.APPROVED if approved else ProposalStatus.REJECTED
        decision = Decision.APPROVE if approved else Decision.REJECT
        if estop_requested:
            decision = Decision.ESTOP
            task.status = TaskStatus.HELD
            robot.status = RobotStatus.ESTOP
            robot.updated_at = utc_now()
        log = ExecutionLog(
            proposal_id=proposal.proposal_id,
            decision=decision,
            triggered_by=TriggerSource.RULE_ENGINE if estop_requested else None,
            rule_results=rule_results,
            hazard_ids=hazard_ids,
            robot_id=proposal.robot_id,
            result_message="안전 규칙 통과" if approved else "안전 규칙 위반으로 거절",
            evaluated_in_ms=(perf_counter() - started_at) * 1000,
        )
        store.logs.append(log)
        return proposal


@app.post("/action-proposals/{proposal_id}/execute", response_model=ActionProposal)
def execute_action(proposal_id: str) -> ActionProposal:
    started_at = perf_counter()
    with store.lock:
        proposal = store.proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")
        if proposal.status != ProposalStatus.APPROVED:
            raise HTTPException(status_code=409, detail="APPROVED 상태만 실행할 수 있습니다.")

        robot = store.robots[proposal.robot_id]
        task = store.tasks[proposal.task_id]
        rule_results, hazard_ids, estop_requested = evaluate_current_safety(proposal, task, robot)
        start_rule_id = "precondition-trajectory-start"
        if (
            proposal.action_type == ActionType.MOVE_TO
            and proposal.model_name == planner.model_name
            and proposal.trajectory
        ):
            start_distance_m = trajectory_start_distance_m(robot, proposal.trajectory[0])
            start_matches = (
                robot.pose.frame_id == proposal.frame_id
                and start_distance_m is not None
                and start_distance_m <= TRAJECTORY_START_TOLERANCE_M
            )
            rule_results.append(RuleResult(
                rule_id=start_rule_id,
                passed=start_matches,
                reason=(
                    f"경로 출발점 허용 오차 이내({start_distance_m:.3f}m)"
                    if start_matches
                    else (
                        "로봇 위치가 경로 출발점과 "
                        f"{TRAJECTORY_START_TOLERANCE_M:.2f}m 넘게 다르거나 좌표계가 달라 "
                        "재계획이 필요합니다."
                    )
                ),
            ))

        evaluated_in_ms = (perf_counter() - started_at) * 1000
        failed_rule_ids = {result.rule_id for result in rule_results if not result.passed}
        if failed_rule_ids:
            requires_replan = failed_rule_ids == {start_rule_id}
            if requires_replan:
                proposal.status = ProposalStatus.REJECTED
                task.status = TaskStatus.PLANNING
            else:
                proposal.status = ProposalStatus.HELD
                task.status = TaskStatus.HELD
            reject_only_rules = {"rule-observation-gap", "rule-safety-distance", start_rule_id}
            requires_estop = estop_requested or bool(failed_rule_ids - reject_only_rules)
            if requires_estop:
                robot.status = RobotStatus.ESTOP
                robot.updated_at = utc_now()
            store.logs.append(
                ExecutionLog(
                    proposal_id=proposal.proposal_id,
                    decision=Decision.ESTOP if requires_estop else Decision.REJECT,
                    rule_results=rule_results,
                    hazard_ids=hazard_ids,
                    robot_id=proposal.robot_id,
                    result_message="실행 직전 안전 재검증 실패",
                    evaluated_in_ms=evaluated_in_ms,
                    triggered_by=TriggerSource.RULE_ENGINE,
                )
            )
            raise HTTPException(status_code=409, detail="실행 직전 안전 재검증에 실패했습니다.")

        try:
            action_dispatcher.dispatch(
                proposal,
                robot,
                list(store.zones.values()),
            )
        except CommandSerializationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DispatchError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        proposal.status = ProposalStatus.EXECUTING
        task.status = TaskStatus.RUNNING
        robot.status = RobotStatus.MOVING
        robot.updated_at = utc_now()
        store.logs.append(
            ExecutionLog(
                proposal_id=proposal.proposal_id,
                decision=Decision.EXECUTE,
                rule_results=rule_results,
                hazard_ids=hazard_ids,
                robot_id=proposal.robot_id,
                result_message=(
                    f"{action_dispatcher.name} adapter({robot.control_adapter})로 전달됨"
                ),
                evaluated_in_ms=evaluated_in_ms,
            )
        )
        return proposal


@app.post("/action-proposals/{proposal_id}/result", response_model=ActionProposal)
def report_action_result(
    proposal_id: str,
    report: AdapterResultReport,
) -> ActionProposal:
    with store.lock:
        proposal = store.proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")

        previous_report = store.proposal_results.get(proposal_id)
        if previous_report is not None:
            if previous_report == report:
                return proposal
            raise HTTPException(
                status_code=409,
                detail="이미 다른 실행 결과가 기록된 ActionProposal입니다.",
            )

        if proposal.status != ProposalStatus.EXECUTING:
            raise HTTPException(status_code=409, detail="EXECUTING 상태만 결과를 보고할 수 있습니다.")

        task = store.tasks[proposal.task_id]
        robot = store.robots[proposal.robot_id]
        if task.status != TaskStatus.RUNNING or robot.status != RobotStatus.MOVING:
            raise HTTPException(
                status_code=409,
                detail="TaskOrder와 Robot이 실행 중 상태가 아니어서 결과를 반영할 수 없습니다.",
            )
        if report.status == AdapterResultStatus.SUCCEEDED:
            proposal.status = ProposalStatus.SUCCEEDED
            task.status = TaskStatus.COMPLETED
            robot.status = RobotStatus.IDLE
            decision = Decision.COMPLETE
            result_message = report.detail or "제어 어댑터 실행 완료"
        else:
            proposal.status = ProposalStatus.FAILED
            decision = Decision.FAIL
            reason_code = AdapterFailureCode(report.reason_code)
            if reason_code == AdapterFailureCode.UNSUPPORTED:
                task.status = TaskStatus.PLANNING
                robot.status = RobotStatus.HOLD
            elif reason_code == AdapterFailureCode.TIMEOUT:
                task.status = TaskStatus.FAILED
                robot.status = RobotStatus.HOLD
            else:
                task.status = TaskStatus.FAILED
                robot.status = RobotStatus.ERROR
            result_message = f"{reason_code.value}: {report.detail or '제어 어댑터 실행 실패'}"

        robot.updated_at = utc_now()
        store.proposal_results[proposal_id] = report
        store.logs.append(
            ExecutionLog(
                proposal_id=proposal.proposal_id,
                decision=decision,
                rule_results=[],
                hazard_ids=[],
                robot_id=proposal.robot_id,
                result_message=result_message,
            )
        )
        return proposal



@app.get("/hazards", response_model=list[HazardEvent])
def list_hazards(
    hazard_status: Annotated[HazardStatus | None, Query(alias="status")] = None,
    severity: Annotated[HazardSeverity | None, Query()] = None,
    robot_id: Annotated[str | None, Query(min_length=1)] = None,
) -> list[HazardEvent]:
    with store.lock:
        hazards = list(store.hazards.values())
        if hazard_status is not None:
            hazards = [hazard for hazard in hazards if hazard.status == hazard_status]
        if severity is not None:
            hazards = [hazard for hazard in hazards if hazard.severity == severity]
        if robot_id is not None:
            hazards = [
                hazard
                for hazard in hazards
                if robot_id in hazard.related_robot_ids
            ]
        return hazards


@app.post(
    "/hazards",
    response_model=HazardEvent,
    status_code=status.HTTP_201_CREATED,
)
def create_hazard(hazard: HazardEvent) -> HazardEvent:
    with store.lock:
        # 1. HazardEvent ID 중복 검사
        if hazard.hazard_id in store.hazards:
            raise HTTPException(
                status_code=409,
                detail="이미 등록된 hazard_id입니다.",
            )

        # 2. 관련 Robot ID 존재 여부 검사
        # 빈 배열은 전역 위험을 뜻하지 않으므로 허용
        unknown_robot_ids = sorted(
            set(hazard.related_robot_ids) - set(store.robots)
        )

        if unknown_robot_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    "등록되지 않은 related_robot_ids: "
                    + ", ".join(unknown_robot_ids)
                ),
            )

        # 3. Zone ID 존재 여부 검사
        if hazard.zone_id not in store.zones:
            raise HTTPException(
                status_code=422,
                detail=f"등록되지 않은 zone_id: {hazard.zone_id}",
            )

        # 4. 유효한 HazardEvent만 저장
        # Observation, Worker는 현재 등록 저장소가 없어 검사 제외
        store.hazards[hazard.hazard_id] = hazard
        stop_robot_ids = robots_to_stop(hazard)
        if stop_robot_ids:
            store.estop_hazard_ids.add(hazard.hazard_id)

    # 실행 중 로봇 즉시 정지. 상태 전이(Robot ESTOP, Proposal·Task HELD)는 CTL ESTOP 이벤트로 반영된다.
    if stop_robot_ids:
        sync_estop(f"{hazard.hazard_id} → {', '.join(stop_robot_ids)}")
    return hazard


@app.post("/hazards/{hazard_id}/resolve", response_model=HazardEvent)
def resolve_hazard(hazard_id: str, request: HazardResolveRequest) -> HazardEvent:
    with store.lock:
        hazard = store.hazards.get(hazard_id)
        if hazard is None:
            raise HTTPException(status_code=404, detail="HazardEvent를 찾을 수 없습니다.")
        if hazard.status == HazardStatus.RESOLVED:
            raise HTTPException(status_code=409, detail="이미 해제된 HazardEvent입니다.")
        if (
            hazard.severity == HazardSeverity.CRITICAL
            and request.resolved_by != HazardResolvedBy.OPERATOR
        ):
            raise HTTPException(
                status_code=409,
                detail="CRITICAL HazardEvent는 OPERATOR만 해제할 수 있습니다.",
            )
        hazard.status = HazardStatus.RESOLVED
        hazard.resolved_at = utc_now()
        hazard.resolved_by = request.resolved_by
        release = hazard_id in store.estop_hazard_ids
        store.estop_hazard_ids.discard(hazard_id)
        release = release and not store.estop_hazard_ids

    # 정지 신호만 푼다. 로봇은 ESTOP에 머물고 복귀는 승인된 RESUME으로 한다(제어 계약 6장).
    if release:
        sync_estop(f"{hazard_id} 해제")
    return hazard


@app.get("/safety-rules", response_model=list[SafetyRule])
def list_safety_rules() -> list[SafetyRule]:
    seed_rules()
    return list(store.rules.values())


@app.get("/execution-logs", response_model=list[ExecutionLog])
def list_execution_logs() -> list[ExecutionLog]:
    return store.logs
