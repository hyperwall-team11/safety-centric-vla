from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Response, status

from .action_planner import ActionPlanningError, RuleBasedActionPlanner
from .models import (
    ActionProposal,
    Decision,
    ExecutionLog,
    HazardEvent,
    HazardStatus,
    NaturalLanguageTaskRequest,
    ProposalGenerationRequest,
    ProposalStatus,
    Robot,
    SafetyRule,
    TaskOrder,
    TaskStatus,
    utc_now,
)
from .safety import default_rules, evaluate_proposal
from .store import store
from .task_parser import TaskParseError, parse_task_order


app = FastAPI(
    title="Safety-Centric VLA Ontology API",
    version="0.2.0",
    description="TaskOrder 변환과 최소 Action 안전 검증을 제공하는 MVP API",
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


def seed_rules() -> None:
    if not store.rules:
        store.rules.update({rule.rule_id: rule for rule in default_rules()})


seed_rules()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.get("/task-orders", response_model=list[TaskOrder])
def list_tasks(
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    target_robot_id: Annotated[str | None, Query(min_length=1)] = None,
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
        return tasks


@app.post(
    "/task-orders/from-natural-language",
    response_model=TaskOrder,
    status_code=status.HTTP_201_CREATED,
)
def create_task_from_natural_language(request: NaturalLanguageTaskRequest) -> TaskOrder:
    with store.lock:
        try:
            task = parse_task_order(request, list(store.robots))
        except TaskParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.tasks[task.task_id] = task
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
    normalized_key = idempotency_key.strip() if idempotency_key is not None else None
    if idempotency_key is not None and not normalized_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key는 비어 있을 수 없습니다.")
    if normalized_key is not None and len(normalized_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key는 128자 이하여야 합니다.")

    request_fingerprint = request.model_dump_json(exclude_none=False)
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
            proposal = planner.plan(task, robot, request)
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


@app.post("/action-proposals/{proposal_id}/evaluate", response_model=ActionProposal)
def evaluate_action(proposal_id: str) -> ActionProposal:
    proposal = store.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")
    if proposal.status != ProposalStatus.PROPOSED:
        raise HTTPException(status_code=409, detail="PROPOSED 상태만 평가할 수 있습니다.")

    robot = store.robots[proposal.robot_id]
    rule_results, hazard_ids = evaluate_proposal(
        proposal,
        robot,
        list(store.hazards.values()),
        list(store.rules.values()),
    )
    approved = all(result.passed for result in rule_results)
    proposal.status = ProposalStatus.APPROVED if approved else ProposalStatus.REJECTED
    log = ExecutionLog(
        proposal_id=proposal.proposal_id,
        decision=Decision.APPROVE if approved else Decision.REJECT,
        rule_results=rule_results,
        hazard_ids=hazard_ids,
        robot_id=proposal.robot_id,
        result_message="안전 규칙 통과" if approved else "안전 규칙 위반으로 거절",
    )
    store.logs.append(log)
    return proposal


@app.post("/action-proposals/{proposal_id}/execute", response_model=ActionProposal)
def execute_action(proposal_id: str) -> ActionProposal:
    proposal = store.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="ActionProposal을 찾을 수 없습니다.")
    if proposal.status != ProposalStatus.APPROVED:
        raise HTTPException(status_code=409, detail="APPROVED 상태만 실행할 수 있습니다.")

    robot = store.robots[proposal.robot_id]
    rule_results, hazard_ids = evaluate_proposal(
        proposal,
        robot,
        list(store.hazards.values()),
        list(store.rules.values()),
    )
    if not all(result.passed for result in rule_results):
        proposal.status = ProposalStatus.HELD
        store.logs.append(
            ExecutionLog(
                proposal_id=proposal.proposal_id,
                decision=Decision.ESTOP,
                rule_results=rule_results,
                hazard_ids=hazard_ids,
                robot_id=proposal.robot_id,
                result_message="실행 직전 안전 재검증 실패",
            )
        )
        raise HTTPException(status_code=409, detail="실행 직전 안전 재검증에 실패했습니다.")

    proposal.status = ProposalStatus.EXECUTING
    store.logs.append(
        ExecutionLog(
            proposal_id=proposal.proposal_id,
            decision=Decision.EXECUTE,
            rule_results=rule_results,
            hazard_ids=[],
            robot_id=proposal.robot_id,
            result_message=f"Mock adapter({robot.control_adapter})로 전달됨",
        )
    )
    return proposal


@app.post("/hazards", response_model=HazardEvent, status_code=status.HTTP_201_CREATED)
def create_hazard(hazard: HazardEvent) -> HazardEvent:
    if hazard.hazard_id in store.hazards:
        raise HTTPException(status_code=409, detail="이미 등록된 hazard_id입니다.")
    store.hazards[hazard.hazard_id] = hazard
    return hazard


@app.post("/hazards/{hazard_id}/resolve", response_model=HazardEvent)
def resolve_hazard(hazard_id: str) -> HazardEvent:
    hazard = store.hazards.get(hazard_id)
    if hazard is None:
        raise HTTPException(status_code=404, detail="HazardEvent를 찾을 수 없습니다.")
    hazard.status = HazardStatus.RESOLVED
    hazard.resolved_at = utc_now()
    return hazard


@app.get("/safety-rules", response_model=list[SafetyRule])
def list_safety_rules() -> list[SafetyRule]:
    seed_rules()
    return list(store.rules.values())


@app.get("/execution-logs", response_model=list[ExecutionLog])
def list_execution_logs() -> list[ExecutionLog]:
    return store.logs
