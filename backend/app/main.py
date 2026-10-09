from __future__ import annotations

from fastapi import FastAPI, HTTPException, status

from .models import (
    ActionProposal,
    Decision,
    ExecutionLog,
    HazardEvent,
    HazardStatus,
    NaturalLanguageTaskRequest,
    ProposalStatus,
    Robot,
    SafetyRule,
    TaskOrder,
    utc_now,
)
from .safety import default_rules, evaluate_proposal
from .store import store
from .task_parser import TaskParseError, parse_task_order


app = FastAPI(
    title="Safety-Centric VLA Ontology API",
    version="0.1.0",
    description="TaskOrder 변환과 최소 Action 안전 검증을 제공하는 MVP API",
)


def seed_rules() -> None:
    if not store.rules:
        store.rules.update({rule.rule_id: rule for rule in default_rules()})


seed_rules()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/robots", response_model=Robot, status_code=status.HTTP_201_CREATED)
def create_robot(robot: Robot) -> Robot:
    if robot.robot_id in store.robots:
        raise HTTPException(status_code=409, detail="이미 등록된 robot_id입니다.")
    store.robots[robot.robot_id] = robot
    return robot


@app.get("/robots", response_model=list[Robot])
def list_robots() -> list[Robot]:
    return list(store.robots.values())


@app.post(
    "/task-orders/from-natural-language",
    response_model=TaskOrder,
    status_code=status.HTTP_201_CREATED,
)
def create_task_from_natural_language(request: NaturalLanguageTaskRequest) -> TaskOrder:
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


@app.post("/action-proposals", response_model=ActionProposal, status_code=status.HTTP_201_CREATED)
def create_proposal(proposal: ActionProposal) -> ActionProposal:
    robot = store.robots.get(proposal.robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="대상 Robot을 찾을 수 없습니다.")
    if proposal.task_id not in store.tasks:
        raise HTTPException(status_code=404, detail="대상 TaskOrder를 찾을 수 없습니다.")
    if robot.status == "ERROR":
        raise HTTPException(status_code=409, detail="ERROR 상태의 로봇에는 제안을 등록할 수 없습니다.")
    if proposal.proposal_id in store.proposals:
        raise HTTPException(status_code=409, detail="이미 등록된 proposal_id입니다.")
    proposal.status = ProposalStatus.PROPOSED
    store.proposals[proposal.proposal_id] = proposal
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
