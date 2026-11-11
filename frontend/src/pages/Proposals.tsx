import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";

export function ProposalList() {
  const tasks = usePolling(() => api.tasks(), 2000);
  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">행동 제안</span><h1>행동은, 검증된<br />다음에.</h1><p>모델이 만든 계획을 규칙별로 확인하고 승인된 행동만 실행합니다.</p></div></div>
      <Panel title="작업 목록">
        <Async state={tasks}>
          {(rows) => (
            <Table rows={rows} rowKey={(t) => t.task_id} empty="작업 없음" columns={[
              { key: "id", label: "작업", render: (t) => <Link to={`/proposals/${t.task_id}`}>{t.task_id}</Link> },
              { key: "robot", label: "로봇", render: (t) => t.target_robot_id },
              { key: "ins", label: "지시", render: (t) => t.instruction },
              { key: "status", label: "상태", render: (t) => <Status value={t.status} /> },
              { key: "at", label: "생성", render: (t) => time(t.created_at) },
            ]} />
          )}
        </Async>
      </Panel>
    </>
  );
}

/** 흐름: 제안(PROPOSED) → [검증] → APPROVED면 [실행] → EXECUTING → SUCCEEDED/FAILED. 거부 사유는 판단 이력에서 */
export function ProposalDetail() {
  const { taskId = "" } = useParams();
  const task = usePolling(() => api.task(taskId), 2000);
  const proposals = usePolling(() => api.taskProposals(taskId), 1000);
  const logs = usePolling(api.executionLogs, 2000);
  const [error, setError] = useState<string>();

  const act = (fn: () => Promise<unknown>) => {
    setError(undefined);
    fn().catch((e) => setError(e instanceof ApiError ? `${e.status}: ${JSON.stringify(e.detail)}` : String(e)))
      .finally(() => { proposals.refresh(); logs.refresh(); });
  };

  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">행동 제안 상세</span><h1>계획을, 하나씩<br />확인합니다.</h1><p>규칙 검사 결과를 확인한 뒤 승인된 행동만 실행합니다.</p></div><Link className="button subtle" to="/proposals">목록으로</Link></div>
      <Panel title="작업">
        <Async state={task}>
          {(t) => (
            <dl className="fields">
              <dt>작업</dt><dd>{t.task_id}</dd>
              <dt>지시</dt><dd>{t.instruction}</dd>
              <dt>로봇</dt><dd>{t.target_robot_id}</dd>
              <dt>상태</dt><dd><Status value={t.status} /></dd>
            </dl>
          )}
        </Async>
        <button className="primary" onClick={() => act(() => api.createProposal(taskId))}>새 행동 제안</button>
      </Panel>
      {error && <p className="error">{error}</p>}
      <Async state={proposals}>
        {(rows) => (
          <>
            {rows.map((p) => {
              const rules = [...(logs.data ?? [])].reverse().find((l) => l.proposal_id === p.proposal_id)?.rule_results ?? [];
              return (
                <Panel className="proposal-card" key={p.proposal_id} title={`${p.action_type} · ${p.proposal_id.slice(0, 17)}`}
                       action={<Status value={p.status} />}>
                  <dl className="fields">
                    <dt>속도 상한</dt><dd>{p.speed_limit} m/s</dd>
                    <dt>목적지</dt><dd><code>{JSON.stringify(p.parameters.target_pose ?? p.parameters)}</code></dd>
                    <dt>모델</dt><dd>{p.model_name} {p.model_version}</dd>
                  </dl>
                  <h3>규칙 검사</h3>
                  <Table rows={rules} rowKey={(r) => r.rule_id} empty="아직 검증하지 않음" columns={[
                    { key: "rule", label: "규칙", render: (r) => r.rule_id },
                    { key: "ok", label: "결과", render: (r) => (r.passed ? "통과" : "위반") },
                    { key: "why", label: "사유", render: (r) => r.reason },
                  ]} />
                  <div className="row">
                    <button className="primary" disabled={p.status !== "PROPOSED"} onClick={() => act(() => api.evaluate(p.proposal_id))}>안전 규칙 검증</button>
                    <button disabled={p.status !== "APPROVED"} onClick={() => act(() => api.execute(p.proposal_id))}>승인된 행동 실행</button>
                  </div>
                </Panel>
              );
            })}
          </>
        )}
      </Async>
    </>
  );
}
