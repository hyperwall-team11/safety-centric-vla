from __future__ import annotations

import asyncio
import logging
import threading
from datetime import timedelta
from enum import Enum
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
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel

from .action_planner import (
    ActionPlanningError,
    ActionProviderError,
    RuleBasedActionPlanner,
)
from .auth import TokenAuthenticator, websocket_bearer_token
from .ctl_bridge import (
    CommandSerializationError,
    DispatchError,
    create_action_dispatcher,
)
from .models import (
    ActionProposal,
    ActionType,
    AdapterAckEvent,
    AdapterAckRecord,
    AdapterAckStatus,
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
    ZoneDestination,
    utc_now,
)
from .safety import WORKER_SNAPSHOT_MAX_AGE_SECONDS, default_rules, evaluate_proposal
from .state_stream import state_broadcaster
from .store import StoreUnavailableError, store
from .task_parser import TaskParseError, parse_task_order, parse_task_order_preview
from .twin_simulation import (
    COLLISION_RULE_ID,
    MAX_SIMULATION_ATTEMPTS,
    TWIN_SIMULATION_ENABLED_ENV,
    HttpTwinSimulator,
    TwinSimulationError,
    TwinSimulator,
    blocked_result,
    build_simulation_request,
    collision_rule_applies,
    compute_snapshot_id,
    reused_pass_result,
    stale_result,
    run_simulation,
)
from .vla_output import VlaOutputValidationError, validate_vla_proposal


app = FastAPI(
    title="Safety-Centric VLA Ontology API",
    version="0.2.0",
    description="TaskOrder 변환과 최소 Action 안전 검증을 제공하는 MVP API",
)
app.state.token_authenticator = TokenAuthenticator.from_environment()
CONSOLE_DIR = Path(__file__).resolve().parent / "static" / "console"
app.mount(
    "/console/assets",
    StaticFiles(directory=CONSOLE_DIR),
    name="console-assets",
)
planner = RuleBasedActionPlanner()
twin_simulator: TwinSimulator = HttpTwinSimulator()
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


def proposal_completes_task(task: TaskOrder, proposal: ActionProposal) -> bool:
    """개별 Action 성공이 전체 TaskOrder의 완료 조건인지 판정한다.

    이동 Task는 단일 MOVE_TO 성공으로 끝난다. 대상 물체가 있는 운반 Task는
    PICK·Navigation 이동 등 중간 Proposal 성공 뒤에도 PLANNING으로 돌아가고,
    최종 PLACE 성공 때만 완료한다.
    """
    if task.target_object_id is None:
        return True
    if proposal.action_type != ActionType.MANIPULATE:
        return False
    return proposal.parameters.get("manipulation") == "PLACE"


class OntologyObjectType(str, Enum):
    ROBOT = "Robot"
    SENSOR = "Sensor"
    WORKER = "Worker"
    ZONE = "Zone"
    TASK_ORDER = "TaskOrder"
    ACTION_PROPOSAL = "ActionProposal"
    HAZARD_EVENT = "HazardEvent"
    SAFETY_RULE = "SafetyRule"
    EXECUTION_LOG = "ExecutionLog"


OntologyObject = (
    Robot
    | Sensor
    | Worker
    | Zone
    | TaskOrder
    | ActionProposal
    | HazardEvent
    | SafetyRule
    | ExecutionLog
)
OBJECT_STORE_ATTRIBUTES = {
    OntologyObjectType.ROBOT: "robots",
    OntologyObjectType.SENSOR: "sensors",
    OntologyObjectType.WORKER: "workers",
    OntologyObjectType.ZONE: "zones",
    OntologyObjectType.TASK_ORDER: "tasks",
    OntologyObjectType.ACTION_PROPOSAL: "proposals",
    OntologyObjectType.HAZARD_EVENT: "hazards",
    OntologyObjectType.SAFETY_RULE: "rules",
    OntologyObjectType.EXECUTION_LOG: "logs",
}
OBJECT_LIST_RESPONSE_HEADERS = {
    "X-Total-Count": {
        "description": "페이지네이션 전 해당 객체 타입의 전체 건수",
        "schema": {"type": "integer", "minimum": 0},
    },
    "X-Observed-At": {
        "description": "Worker 전체 스냅샷의 관측 시각(Worker 조회에만 포함)",
        "schema": {"type": "string", "format": "date-time"},
    },
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
TRAJECTORY_START_RULE_ID = "precondition-trajectory-start"
# 개발계획서 v1.0 KPI 9의 명령 입력→어댑터 ACK 목표 시간과 같다.
ADAPTER_ACK_TIMEOUT_SECONDS = 1.0
# 조회용 ACK 이력은 유지하되 장시간 운영에서 무한히 늘지 않게 한다.
MAX_ACK_RECORDS = 1000
STATE_HEARTBEAT_SECONDS = 15.0
# UI 상태 스트림 부하 제한이며 ROS 2 안전 heartbeat·판정 주기와 무관하다.
STATE_MIN_PUSH_INTERVAL_SECONDS = 0.2
STATE_EXECUTION_LOG_LIMIT = 100
NON_MUTATING_POST_PATHS = {
    "/task-orders/preview",
    "/task-orders/preview/batch",
}
PUBLIC_HTTP_PATHS = {
    "/health",
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
    "/console",
}
PUBLIC_HTTP_PREFIXES = ("/console/assets/",)
STATE_WEBSOCKET_PROTOCOL = "state.v1"


class AuthStatus(BaseModel):
    enabled: bool
    subject: str | None = None
    role: str | None = None


def build_openapi_schema() -> dict:
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    security_schemes = schema.setdefault("components", {}).setdefault(
        "securitySchemes",
        {},
    )
    security_schemes["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "BACKEND_API_TOKEN_CONFIG가 설정된 경우 필요한 사전 발급 API 토큰"
        ),
    }
    for path, operations in schema["paths"].items():
        for operation in operations.values():
            if isinstance(operation, dict):
                operation["security"] = [] if path == "/health" else [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = build_openapi_schema


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


def is_public_http_path(path: str) -> bool:
    return path in PUBLIC_HTTP_PATHS or path.startswith(PUBLIC_HTTP_PREFIXES)


@app.middleware("http")
async def authenticate_api_token(request: Request, call_next):
    authenticator: TokenAuthenticator = app.state.token_authenticator
    request.state.auth_principal = None
    if not authenticator.enabled or is_public_http_path(request.url.path):
        return await call_next(request)
    principal = authenticator.authenticate_authorization(
        request.headers.get("authorization")
    )
    if principal is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "유효한 Bearer API 토큰이 필요합니다."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.auth_principal = principal
    return await call_next(request)


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
    authenticator: TokenAuthenticator = app.state.token_authenticator
    principal = None
    if authenticator.enabled:
        token = websocket_bearer_token(
            websocket.headers.get("authorization"),
            websocket.headers.get("sec-websocket-protocol"),
        )
        principal = authenticator.authenticate_token(token)
        if principal is None:
            await websocket.close(
                code=4401,
                reason="유효한 Bearer API 토큰이 필요합니다.",
            )
            return
    websocket.state.auth_principal = principal
    requested_protocols = {
        protocol.strip()
        for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
    }
    selected_protocol = (
        STATE_WEBSOCKET_PROTOCOL
        if STATE_WEBSOCKET_PROTOCOL in requested_protocols
        else None
    )
    await websocket.accept(subprotocol=selected_protocol)
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
    # 저장소에 규칙이 이미 있어도(영속 저장소 복구 등) 새로 추가된 기본 규칙은 빠뜨리지 않는다. 기존 규칙은 덮어쓰지 않는다.
    with store.lock:
        rules = default_rules(
            perception_enabled=getenv("BACKEND_PERCEPTION_SAFETY_ENABLED") == "1",
            collision_simulation_enabled=getenv(TWIN_SIMULATION_ENABLED_ENV) == "1",
        )
        for rule in rules:
            store.rules.setdefault(rule.rule_id, rule)


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
        store.synchronize_safety_publish(
            lambda active: _publish_estop(active, reason)
        )


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


def apply_ctl_ack_event(
    ack: AdapterAckEvent,
    received_at: AwareDatetime | None = None,
) -> None:
    """CTL ACK 첫 수신 기록을 보존하고 중복은 개수만 누적한다."""
    received_at = received_at or utc_now()
    with store.lock:
        record = store.proposal_acks.get(ack.proposal_id)
        if record is None:
            LOGGER.warning("발행 기록이 없는 CTL ACK를 무시했습니다: %s", ack.proposal_id)
            return
        if record.robot_id != ack.robot_id:
            LOGGER.warning("대상 Robot이 일치하지 않는 CTL ACK를 무시했습니다: %s", ack.proposal_id)
            return
        if record.ack_received_at is not None:
            record.duplicate_count += 1
            return

        record.adapter_ack_at = ack.timestamp
        record.ack_received_at = received_at
        record.command_to_ack_ms = max(
            0.0,
            (received_at - record.command_received_at).total_seconds() * 1000,
        )
        record.dispatch_to_ack_ms = max(
            0.0,
            (received_at - record.dispatched_at).total_seconds() * 1000,
        )
        record.status = (
            AdapterAckStatus.ACKNOWLEDGED
            if received_at <= record.timeout_at
            else AdapterAckStatus.TIMED_OUT
        )


action_dispatcher = create_action_dispatcher()
if hasattr(action_dispatcher, "safety_event_handler"):
    action_dispatcher.safety_event_handler = apply_ctl_safety_event
if hasattr(action_dispatcher, "ack_event_handler"):
    action_dispatcher.ack_event_handler = apply_ctl_ack_event


@app.on_event("startup")
def start_action_dispatcher() -> None:
    action_dispatcher.start()
    store.start_change_notifications(state_broadcaster.publish)
    # Redis 재시작 복구 시 latched E-Stop 상태도 CTL에 다시 전달한다.
    sync_estop("Backend 시작 상태 복구")


@app.on_event("shutdown")
def stop_action_dispatcher() -> None:
    store.stop_change_notifications()
    action_dispatcher.stop()


@app.exception_handler(StoreUnavailableError)
def store_unavailable_handler(
    request: Request,
    exc: StoreUnavailableError,
) -> JSONResponse:
    # 읽기 실패나 잠금 경합은 503만 반환한다. 명령을 이미 발행한 요청의
    # 커밋이 실패했을 때만 물리 실행과 Backend 상태 불일치를 막기 위해 정지한다.
    if getattr(request.state, "estop_on_store_failure", False):
        with ESTOP_PUBLISH_LOCK:
            _publish_estop(True, f"Redis 실행 상태 커밋 실패: {exc}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
    )


@app.get("/health")
def health() -> dict[str, str]:
    store.ensure_available()
    return {"status": "ok"}


@app.get("/auth/me", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    authenticator: TokenAuthenticator = app.state.token_authenticator
    principal = request.state.auth_principal
    if principal is None:
        return AuthStatus(enabled=authenticator.enabled)
    return AuthStatus(
        enabled=True,
        subject=principal.subject,
        role=principal.role,
    )


@app.get(
    "/api/v1/objects/{type}",
    response_model=list[OntologyObject],
    responses={
        status.HTTP_200_OK: {
            "description": "페이지네이션된 온톨로지 객체 목록",
            "headers": OBJECT_LIST_RESPONSE_HEADERS,
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Worker 관측 스냅샷이 아직 없음",
        },
    },
)
def list_ontology_objects(
    type: OntologyObjectType,
    response: Response,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OntologyObject]:
    if type == OntologyObjectType.SAFETY_RULE:
        seed_rules()
    with store.lock:
        collection = getattr(store, OBJECT_STORE_ATTRIBUTES[type])
        if collection is None:
            if type == OntologyObjectType.WORKER:
                raise HTTPException(
                    status_code=404,
                    detail="Worker 관측 스냅샷이 아직 없습니다.",
                )
            collection = []
        if isinstance(collection, dict):
            collection = list(collection.values())
        else:
            collection = list(collection)
        response.headers["X-Total-Count"] = str(len(collection))
        if type == OntologyObjectType.WORKER:
            observed_at = store.workers_observed_at
            if observed_at is None:
                raise HTTPException(
                    status_code=404,
                    detail="Worker 관측 스냅샷이 아직 없습니다.",
                )
            response.headers["X-Observed-At"] = observed_at.isoformat()
        return [
            item.model_copy(deep=True)
            for item in collection[offset : offset + limit]
        ]


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
    with store.lock:
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
    with store.lock:
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
    command_received_at = utc_now()
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
        store.task_received_at[task.task_id] = command_received_at
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
    with store.lock:
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

        destination_zone = store.zones.get(getattr(task.destination, "zone_id", None))
        task_snapshot = task.model_copy(deep=True)
        robot_snapshot = robot.model_copy(deep=True)
        destination_zone_snapshot = (
            None if destination_zone is None else destination_zone.model_copy(deep=True)
        )
        selected_planner = planner

    # VLA 추론은 수 초 이상 걸릴 수 있으므로 위험 이벤트와 상태 갱신을 막지 않게
    # 저장소 잠금 밖에서 실행한다.
    try:
        proposal = selected_planner.plan(
            task_snapshot,
            robot_snapshot,
            request,
            destination_zone=destination_zone_snapshot,
        )
    except ActionPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ActionProviderError as exc:
        LOGGER.warning("VLA 추론 요청 실패: %s", exc.__cause__ or exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    with store.lock:
        # 동일 멱등 요청이 추론 중 먼저 완료됐다면 그 결과를 재사용한다.
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

        task = store.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="TaskOrder를 찾을 수 없습니다.")
        robot = store.robots.get(task.target_robot_id)
        if robot is None:
            raise HTTPException(status_code=404, detail="TaskOrder의 대상 Robot을 찾을 수 없습니다.")
        destination_zone = store.zones.get(getattr(task.destination, "zone_id", None))
        if (
            task != task_snapshot
            or robot != robot_snapshot
            or destination_zone != destination_zone_snapshot
        ):
            raise HTTPException(
                status_code=409,
                detail="VLA 추론 중 TaskOrder·Robot·Zone 문맥이 변경되어 결과를 저장하지 않습니다.",
            )
        if task.status not in PLANNABLE_TASK_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"{task.status} 상태의 TaskOrder에서는 제안을 생성할 수 없습니다.",
            )
        if robot.status == "ERROR":
            raise HTTPException(status_code=409, detail="ERROR 상태의 로봇에는 제안을 생성할 수 없습니다.")
        active_proposal = next(
            (
                existing
                for existing in store.proposals.values()
                if existing.task_id == task_id
                and existing.status in ACTIVE_PROPOSAL_STATUSES
            ),
            None,
        )
        if active_proposal is not None:
            raise HTTPException(
                status_code=409,
                detail=f"이 TaskOrder에는 활성 ActionProposal이 이미 있습니다: {active_proposal.proposal_id}",
            )

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
        task = store.tasks.get(proposal.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="대상 TaskOrder를 찾을 수 없습니다.")
        if proposal.robot_id != task.target_robot_id:
            raise HTTPException(
                status_code=422,
                detail="ActionProposal의 robot_id가 TaskOrder의 대상 로봇과 다릅니다.",
            )
        robot = store.robots.get(proposal.robot_id)
        if robot is None:
            raise HTTPException(status_code=404, detail="대상 Robot을 찾을 수 없습니다.")
        if task.status not in PLANNABLE_TASK_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"{task.status} 상태의 TaskOrder에는 제안을 등록할 수 없습니다.",
            )
        if robot.status == "ERROR":
            raise HTTPException(status_code=409, detail="ERROR 상태의 로봇에는 제안을 등록할 수 없습니다.")
        destination_zone = (
            store.zones.get(task.destination.zone_id)
            if isinstance(task.destination, ZoneDestination)
            else None
        )
        try:
            validate_vla_proposal(
                proposal,
                task,
                robot,
                destination_zone=destination_zone,
            )
        except VlaOutputValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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


def needs_collision_simulation(
    proposal: ActionProposal, rule_results: list[RuleResult], estop_requested: bool
) -> bool:
    """스키마 v1.1 5장 9번: 규칙 1~8을 모두 통과한 후보만 사전 시뮬레이션한다. 잠금 안에서 부른다."""
    rule = store.rules.get(COLLISION_RULE_ID)
    return (
        rule is not None
        and rule.enabled
        and collision_rule_applies(proposal.action_type)
        and not estop_requested
        and all(result.passed for result in rule_results)
    )


def current_snapshot_id() -> str:
    """제어 계약 13.3절 snapshot_id. 잠금 안에서 부른다."""
    return compute_snapshot_id(
        store.robots.values(),
        store.workers,
        store.workers_observed_at,
        store.hazards.values(),
        store.zones.values(),
    )


def prepare_collision_simulation(
    proposal: ActionProposal, robot: Robot, snapshot_id: str
) -> tuple[dict | None, RuleResult | None]:
    """잠금 안에서 요청 본문을 값으로 복사해 둔다. 만들 수 없으면 차단 결과를 돌려준다."""
    try:
        payload = build_simulation_request(
            proposal,
            robot,
            robots=list(store.robots.values()),
            workers=None if store.workers is None else list(store.workers),
            workers_observed_at=store.workers_observed_at,
            zones=list(store.zones.values()),
            snapshot_id=snapshot_id,
            snapshot_at=utc_now(),
        )
    except TwinSimulationError as exc:
        return None, blocked_result(f"사전 시뮬레이션 요청 불가로 차단: {exc}", snapshot_id)
    except Exception as exc:  # 요청을 만들 수 없으면 통과로 보지 않는다(Fail-Closed)
        return None, blocked_result(
            f"사전 시뮬레이션 요청 불가로 차단: {type(exc).__name__}: {exc}", snapshot_id
        )
    return payload, None


def select_collision_result(
    proposal: ActionProposal,
    robot: Robot,
    snapshot_id: str,
    simulated: tuple[str, RuleResult] | None,
    attempts: int,
    stored_pass_snapshot_id: str | None = None,
) -> tuple[dict | None, RuleResult | None]:
    """현재 snapshot_id에 쓸 수 있는 COLLISION 결과를 고르고, 없으면 새 요청 본문을 만든다. 잠금 안에서 부른다.

    제어 계약 13.3절: 다른 snapshot_id의 PASS는 쓰지 않는다. 위반 결과는 상태가 바뀌어도 그대로 차단한다.
    """
    if simulated is not None and (simulated[0] == snapshot_id or not simulated[1].passed):
        return None, simulated[1]
    if stored_pass_snapshot_id == snapshot_id:
        return None, reused_pass_result(snapshot_id)
    if attempts >= MAX_SIMULATION_ATTEMPTS:
        return None, stale_result(snapshot_id, attempts)
    return prepare_collision_simulation(proposal, robot, snapshot_id)


def load_proposal_for_update(
    proposal_id: str, expected_status: ProposalStatus, detail: str
) -> tuple[ActionProposal, Robot, TaskOrder]:
    proposal = store.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")
    if proposal.status != expected_status:
        raise HTTPException(status_code=409, detail=detail)
    return proposal, store.robots[proposal.robot_id], store.tasks[proposal.task_id]


def finish_evaluation(
    proposal: ActionProposal,
    robot: Robot,
    task: TaskOrder,
    rule_results: list[RuleResult],
    hazard_ids: list[str],
    estop_requested: bool,
    started_at: float,
) -> ActionProposal:
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


@app.post("/action-proposals/{proposal_id}/evaluate", response_model=ActionProposal)
def evaluate_action(proposal_id: str) -> ActionProposal:
    started_at = perf_counter()
    not_proposed = "PROPOSED 상태만 평가할 수 있습니다."
    attempts = 0
    simulated: tuple[str, RuleResult] | None = None  # (시뮬레이션한 snapshot_id, 결과)
    while True:
        with store.lock:
            proposal, robot, task = load_proposal_for_update(
                proposal_id, ProposalStatus.PROPOSED, not_proposed
            )
            # 시뮬레이션 대기 중 바뀐 위험·로봇 상태도 결정에 반영한다.
            rule_results, hazard_ids, estop_requested = evaluate_current_safety(
                proposal, task, robot
            )
            payload = None
            pass_snapshot_id = None
            if needs_collision_simulation(proposal, rule_results, estop_requested):
                # 평가 완료 시점의 snapshot_id와 같은 상태로 시뮬레이션한 결과만 쓴다.
                snapshot_id = current_snapshot_id()
                payload, collision_result = select_collision_result(
                    proposal, robot, snapshot_id, simulated, attempts
                )
                if collision_result is not None:
                    rule_results.append(collision_result)
                    if collision_result.passed:
                        pass_snapshot_id = snapshot_id
            if payload is None:
                proposal = finish_evaluation(
                    proposal, robot, task, rule_results, hazard_ids, estop_requested, started_at
                )
                if proposal.status == ProposalStatus.APPROVED and pass_snapshot_id is not None:
                    store.twin_simulation_passes[proposal_id] = pass_snapshot_id
                return proposal

        # 제어 계약 13.3절: HTTP 응답을 기다리는 동안 저장소 잠금을 잡지 않는다.
        attempts += 1
        simulated = (payload["snapshot_id"], run_simulation(twin_simulator, payload))


def evaluate_execution_checks(
    proposal: ActionProposal, task: TaskOrder, robot: Robot
) -> tuple[list[RuleResult], list[str], bool]:
    """실행 직전 재검증(규칙 1~8 + 경로 출발점). 잠금 안에서 부른다."""
    rule_results, hazard_ids, estop_requested = evaluate_current_safety(proposal, task, robot)
    if (
        proposal.action_type == ActionType.MOVE_TO
        and proposal.trajectory
    ):
        start_distance_m = trajectory_start_distance_m(robot, proposal.trajectory[0])
        start_matches = (
            robot.pose.frame_id == proposal.frame_id
            and start_distance_m is not None
            and start_distance_m <= TRAJECTORY_START_TOLERANCE_M
        )
        rule_results.append(RuleResult(
            rule_id=TRAJECTORY_START_RULE_ID,
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
    return rule_results, hazard_ids, estop_requested


def finish_execution(
    proposal: ActionProposal,
    robot: Robot,
    task: TaskOrder,
    rule_results: list[RuleResult],
    hazard_ids: list[str],
    estop_requested: bool,
    started_at: float,
    http_request: Request,
) -> ActionProposal:
    # 평가 단계 PASS는 이번 실행 판단에만 쓸 수 있다.
    store.twin_simulation_passes.pop(proposal.proposal_id, None)
    evaluated_in_ms = (perf_counter() - started_at) * 1000
    failed_rule_ids = {result.rule_id for result in rule_results if not result.passed}
    if failed_rule_ids:
        requires_replan = failed_rule_ids == {TRAJECTORY_START_RULE_ID}
        if requires_replan:
            proposal.status = ProposalStatus.REJECTED
            task.status = TaskStatus.PLANNING
        else:
            proposal.status = ProposalStatus.HELD
            task.status = TaskStatus.HELD
        reject_only_rules = {
            "rule-observation-gap",
            "rule-safety-distance",
            TRAJECTORY_START_RULE_ID,
            # 시뮬레이션 실패·트윈 미가동은 실행 거부이며, 아직 움직이지 않은 로봇을 E-Stop하지 않는다.
            COLLISION_RULE_ID,
        }
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

    # Redis 저장소가 끊긴 상태에서 캐시된 승인 명령을 로봇에 보내지 않는다.
    store.ensure_available()
    try:
        dispatched_at = utc_now()
        if len(store.proposal_acks) >= MAX_ACK_RECORDS:
            removable = next(
                (
                    candidate_id
                    for candidate_id in store.proposal_acks
                    if store.proposals.get(candidate_id) is None
                    or store.proposals[candidate_id].status != ProposalStatus.EXECUTING
                ),
                None,
            )
            if removable is not None:
                store.proposal_acks.pop(removable, None)
        store.proposal_acks[proposal.proposal_id] = AdapterAckRecord(
            proposal_id=proposal.proposal_id,
            task_id=task.task_id,
            robot_id=robot.robot_id,
            command_received_at=store.task_received_at.get(task.task_id, task.created_at),
            dispatched_at=dispatched_at,
            timeout_at=dispatched_at + timedelta(seconds=ADAPTER_ACK_TIMEOUT_SECONDS),
        )
        action_dispatcher.dispatch(
            proposal,
            robot,
            list(store.zones.values()),
        )
        http_request.state.estop_on_store_failure = True
    except CommandSerializationError as exc:
        store.proposal_acks.pop(proposal.proposal_id, None)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DispatchError as exc:
        store.proposal_acks.pop(proposal.proposal_id, None)
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


@app.post("/action-proposals/{proposal_id}/execute", response_model=ActionProposal)
def execute_action(proposal_id: str, http_request: Request) -> ActionProposal:
    started_at = perf_counter()
    not_approved = "APPROVED 상태만 실행할 수 있습니다."
    attempts = 0
    simulated: tuple[str, RuleResult] | None = None  # (시뮬레이션한 snapshot_id, 결과)
    while True:
        with store.lock:
            proposal, robot, task = load_proposal_for_update(
                proposal_id, ProposalStatus.APPROVED, not_approved
            )
            rule_results, hazard_ids, estop_requested = evaluate_execution_checks(
                proposal, task, robot
            )
            payload = None
            if needs_collision_simulation(proposal, rule_results, estop_requested):
                # 제어 계약 13.3절: 실행 직전 상태로 snapshot_id를 다시 계산해 같은 상태의 PASS만 쓴다.
                snapshot_id = current_snapshot_id()
                payload, collision_result = select_collision_result(
                    proposal,
                    robot,
                    snapshot_id,
                    simulated,
                    attempts,
                    store.twin_simulation_passes.get(proposal_id),
                )
                if collision_result is not None:
                    rule_results.append(collision_result)
            if payload is None:
                return finish_execution(
                    proposal,
                    robot,
                    task,
                    rule_results,
                    hazard_ids,
                    estop_requested,
                    started_at,
                    http_request,
                )

        # 잠금 밖에서 기다려 대기 중에도 위험 이벤트 등록과 긴급 정지가 지연되지 않게 한다.
        attempts += 1
        simulated = (payload["snapshot_id"], run_simulation(twin_simulator, payload))


@app.get(
    "/action-proposals/{proposal_id}/ack",
    response_model=AdapterAckRecord,
)
def get_action_ack(proposal_id: str) -> AdapterAckRecord:
    with store.lock:
        if proposal_id not in store.proposals:
            raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")
        record = store.proposal_acks.get(proposal_id)
        if record is None:
            raise HTTPException(status_code=404, detail="ActionProposal의 ACK 대기 기록을 찾을 수 없습니다.")
        if (
            record.status == AdapterAckStatus.PENDING
            and utc_now() > record.timeout_at
        ):
            record.status = AdapterAckStatus.TIMED_OUT
        return record.model_copy(deep=True)


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
            task_completed = proposal_completes_task(task, proposal)
            task.status = TaskStatus.COMPLETED if task_completed else TaskStatus.PLANNING
            robot.status = RobotStatus.IDLE
            decision = Decision.COMPLETE
            result_message = report.detail or "제어 어댑터 실행 완료"
            if not task_completed:
                result_message = f"{result_message}; 다음 ActionProposal 대기"
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
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            store.task_received_at.pop(task.task_id, None)
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
    with store.lock:
        return list(store.rules.values())


@app.get("/execution-logs", response_model=list[ExecutionLog])
def list_execution_logs() -> list[ExecutionLog]:
    with store.lock:
        return list(store.logs)
