import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";
import { ProposalReview, latestLog, planOverlay } from "../components/ProposalReview";
import { SiteMap } from "./SiteMap";

export function ProposalList() {
  const tasks = usePolling(() => api.tasks(), 2000);
  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">행동 제안</span><h1>행동은, 검증된 다음에.</h1><p>모델이 만든 계획을 규칙별로 확인하고 승인된 행동만 실행합니다.</p></div></div>
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

/** 흐름: 제안(PROPOSED) → [검증] → APPROVED면 [실행] → EXECUTING → SUCCEEDED/FAILED. 위반 위치는 지도에 표시 */
export function ProposalDetail() {
  const { taskId = "" } = useParams();
  const task = usePolling(() => api.task(taskId), 2000);
  const proposals = usePolling(() => api.taskProposals(taskId), 1000);
  const logs = usePolling(api.executionLogs, 2000);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [focusId, setFocusId] = useState<string>();

  const act = (fn: () => Promise<unknown>) => {
    setError(undefined);
    setBusy(true);
    fn().catch((e) => setError(e instanceof ApiError ? `${e.status}: ${JSON.stringify(e.detail)}` : String(e)))
      .finally(() => { setBusy(false); proposals.refresh(); logs.refresh(); });
  };
  // 최신 제안이 위로. 지도는 고른 제안(없으면 최신)을 보여 준다.
  const sorted = [...(proposals.data ?? [])].sort((a, b) => b.proposed_at.localeCompare(a.proposed_at));
  const shown = sorted.find((p) => p.proposal_id === focusId) ?? sorted[0];
  const overlay = planOverlay(task.data, shown, latestLog(logs.data, shown?.proposal_id));

  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">행동 제안 상세</span><h1>계획을, 하나씩 확인합니다.</h1><p>규칙 검사 결과를 확인한 뒤 승인된 행동만 실행합니다.</p></div><Link className="button subtle" to="/proposals">목록으로</Link></div>
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
      <div className="request-layout">
        <Panel title="현장 지도" className="map-panel">
          <SiteMap selectedRobotId={shown?.robot_id}
                   selectedZoneId={task.data?.destination.type === "ZONE" ? task.data.destination.zone_id : undefined}
                   plan={overlay.plan} violation={overlay.violation} />
          <p className="map-hint">{shown ? `${shown.proposal_id.slice(0, 17)} 제안의 이동과 위반 위치` : "제안이 없습니다."}</p>
        </Panel>
        <div className="stack">
          <Async state={proposals}>
            {() => (
              <>
                {sorted.length === 0 && <p className="empty-state">아직 행동 제안이 없습니다.</p>}
                {sorted.map((p) => (
                  <Panel className={`proposal-card${p === shown ? " is-shown" : ""}`} key={p.proposal_id}
                         title={`${p.action_type} · ${p.proposal_id.slice(0, 17)}`}
                         action={p === shown ? <span className="stat-detail">지도에 표시 중</span>
                                             : <button className="compact-button" onClick={() => setFocusId(p.proposal_id)}>지도에 표시</button>}>
                    <small className="muted">{time(p.proposed_at)} 제안 · {p.model_name} {p.model_version}</small>
                    <ProposalReview proposal={p} log={latestLog(logs.data, p.proposal_id)} busy={busy} act={act} />
                  </Panel>
                ))}
              </>
            )}
          </Async>
        </div>
      </div>
    </>
  );
}
