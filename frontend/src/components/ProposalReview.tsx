import { Link } from "react-router-dom";
import { api } from "../api/client";
import { explainRule, trajectoryPoints } from "../api/explain";
import type { ActionProposal, ExecutionLog, TaskOrder } from "../api/types";
import { Status } from "./ui";

/** 제안에 대한 가장 최근 판단(검증·실행 재검증). */
export const latestLog = (logs: ExecutionLog[] | undefined, proposalId: string | undefined) =>
  proposalId ? [...(logs ?? [])].reverse().find((l) => l.proposal_id === proposalId) : undefined;

/** 지도에 그릴 계획 경로와 위반 위치. */
export function planOverlay(task: TaskOrder | undefined, proposal: ActionProposal | undefined, log: ExecutionLog | undefined) {
  if (!proposal) return {};
  const failed = (log?.rule_results ?? []).filter((r) => !r.passed).map(explainRule);
  const status = proposal.status === "REJECTED" || failed.length > 0
    ? "blocked" as const
    : proposal.status === "PROPOSED" ? "pending" as const : "passed" as const;
  return {
    plan: {
      robotId: proposal.robot_id,
      path: trajectoryPoints(proposal),
      toZoneId: task?.destination.type === "ZONE" ? task.destination.zone_id : undefined,
      status,
    },
    violation: { zoneIds: failed.flatMap((f) => f.zoneIds), workerIds: failed.flatMap((f) => f.workerIds) },
  };
}

/** 규칙별 결과와 위반 시 해결 방법, 검증·실행 버튼. */
export function ProposalReview({ proposal, log, busy, act }: {
  proposal: ActionProposal;
  log: ExecutionLog | undefined;
  busy: boolean;
  act: (fn: () => Promise<unknown>) => void;
}) {
  const rules = log?.rule_results ?? [];
  const failed = rules.filter((r) => !r.passed);
  // 위반을 먼저 보여 준다.
  const ordered = [...failed, ...rules.filter((r) => r.passed)];

  return (
    <div className="review">
      <div className="review-head">
        <Status value={proposal.status} />
        <span>
          {proposal.status === "PROPOSED" && "아직 검증하지 않았습니다."}
          {proposal.status === "APPROVED" && "모든 안전 규칙을 통과했습니다. 실행할 수 있습니다."}
          {proposal.status === "REJECTED" && `안전 규칙 ${failed.length}건 위반으로 거절되었습니다.`}
          {proposal.status === "EXECUTING" && "실행 중입니다."}
          {proposal.status === "SUCCEEDED" && "실행을 완료했습니다."}
          {(proposal.status === "FAILED" || proposal.status === "HELD") && (log?.result_message ?? "실행이 중단되었습니다.")}
        </span>
        <small>속도 상한 {proposal.speed_limit} m/s{log?.evaluated_in_ms != null && ` · 판정 ${log.evaluated_in_ms.toFixed(1)}ms`}</small>
      </div>

      {ordered.length > 0 && (
        <ul className="rule-checks">
          {ordered.map((r) => {
            const ex = explainRule(r);
            return (
              <li key={r.rule_id} data-passed={r.passed}>
                <span className="mark" aria-label={r.passed ? "통과" : "위반"}>{r.passed ? "✓" : "✕"}</span>
                <div>
                  <strong>{ex.label}</strong>
                  <small>{r.reason}</small>
                  {ex.hint && <p className="hint">{ex.hint}</p>}
                  {(ex.link || ex.allowedSpeed !== undefined) && (
                    <div className="row">
                      {ex.allowedSpeed !== undefined && (
                        <button className="compact-button" disabled={busy}
                                onClick={() => act(() => api.createProposal(proposal.task_id, ex.allowedSpeed))}>
                          {ex.allowedSpeed} m/s로 다시 제안
                        </button>
                      )}
                      {ex.link && <Link className="button compact-button" to={ex.link.to}>{ex.link.label}</Link>}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="row">
        {proposal.status === "PROPOSED" && (
          <button className="primary" disabled={busy} onClick={() => act(() => api.evaluate(proposal.proposal_id))}>안전 규칙 검증</button>
        )}
        {proposal.status === "APPROVED" && (
          <button className="primary" disabled={busy} onClick={() => act(() => api.execute(proposal.proposal_id))}>승인된 행동 실행</button>
        )}
        {proposal.status === "REJECTED" && (
          <button disabled={busy} onClick={() => act(() => api.createProposal(proposal.task_id))}>새 행동 제안</button>
        )}
      </div>
    </div>
  );
}
