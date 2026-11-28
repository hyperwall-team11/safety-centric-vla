import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";

/** 흐름: 정지(ESTOP·HOLD) 확인 → 위험 해제 → RESUME 행동 제안 → 검증·실행. 해제만으로 로봇이 움직이지 않는다. */
export function EStop() {
  const navigate = useNavigate();
  const robots = usePolling(api.robots);
  const tasks = usePolling(() => api.tasks("HELD"), 2000);
  const logs = usePolling(api.executionLogs, 2000);
  const auth = usePolling(api.authStatus, 5000, false);
  const estop = usePolling(api.estopStatus, 1000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const stops = (logs.data ?? []).filter((l) => l.decision === "ESTOP").reverse();
  const operator = auth.data?.enabled === false || ["OPERATOR", "ADMIN"].includes(auth.data?.role ?? "");
  const resume = (taskId: string) => {
    setBusy(true);
    setError(undefined);
    api.createResumeProposal(taskId)
      .then(() => navigate(`/proposals/${taskId}`))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  };

  const act = (action: "trigger" | "release") => {
    const message = action === "trigger"
      ? "전체 로봇에 비상정지를 발행할까요? 실행 중 작업은 즉시 보류됩니다."
      : "운영자 비상정지 신호를 해제할까요? 로봇은 자동으로 재개되지 않습니다.";
    if (!window.confirm(message)) return;
    setBusy(true);
    setError(undefined);
    const request = action === "trigger"
      ? api.triggerEstop("운영자 콘솔 수동 비상정지")
      : api.releaseEstop();
    request
      .catch((e) => setError(e instanceof ApiError ? `${e.status}: ${JSON.stringify(e.detail)}` : String(e)))
      .finally(() => {
        setBusy(false);
        estop.refresh();
        robots.refresh();
        tasks.refresh();
        logs.refresh();
      });
  };

  return (
    <>
      <div className="page-head">
        <div className="page-head-copy"><span className="eyebrow">비상정지 · 복귀</span><h1>해제와 복귀는 분리합니다.</h1><p>CV 안전 확인, 관리자 위험 해제, RESUME 규칙 검증을 모두 통과해야 ROS 2 복귀 명령이 발행됩니다.</p></div>
        <div className="estop-actions">
          <button className="danger" disabled={busy || estop.data?.operator_active === true} onClick={() => act("trigger")}>전체 비상정지</button>
          <button className="button subtle" disabled={busy || estop.data?.operator_active !== true} onClick={() => act("release")}>정지 신호 해제</button>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <Panel title="비상정지 신호">
        <Async state={estop}>
          {(current) => (
            <dl className="fields">
              <dt>현재 신호</dt><dd><Status value={current.active ? "ESTOP" : "IDLE"} /></dd>
              <dt>운영자 정지</dt><dd>{current.operator_active ? "활성" : "비활성"}</dd>
              <dt>위험 연동</dt><dd>{current.hazard_ids.join(", ") || "없음"}</dd>
              <dt>정지 사유</dt><dd>{current.reason ?? "-"}</dd>
              <dt>요청자</dt><dd>{current.requested_by ?? "로컬 운영자"}</dd>
              <dt>발행 시각</dt><dd>{current.issued_at ? time(current.issued_at) : "-"}</dd>
            </dl>
          )}
        </Async>
      </Panel>
      <Panel title="정지 중 로봇">
        <Async state={robots}>
          {(rows) => (
            <Table rows={rows.filter((r) => r.status === "ESTOP" || r.status === "HOLD")} rowKey={(r) => r.robot_id}
                   empty="정지 중인 로봇 없음" columns={[
              { key: "id", label: "로봇", render: (r) => r.robot_id },
              { key: "status", label: "상태", render: (r) => <Status value={r.status} /> },
              { key: "at", label: "변경", render: (r) => time(r.updated_at) },
            ]} />
          )}
        </Async>
      </Panel>
      <Panel title="복귀 순서" className="recovery-panel">
        <ol className="steps">
          <li>CV가 모든 감시 카메라와 Worker 스냅샷으로 2초간 안전 상태 확인</li>
          <li><Link to="/hazards">위험 모니터링</Link>에서 관리자 권한으로 관련 위험 해제</li>
          <li>운영자 정지를 사용했다면 위의 정지 신호 해제</li>
          <li>RESUME 제안을 다시 검증·실행해 로봇을 IDLE로 복귀</li>
        </ol>
      </Panel>
      <Panel title="보류 작업">
        <Async state={tasks}>
          {(rows) => (
            <Table rows={rows} rowKey={(t) => t.task_id} empty="보류 작업 없음" columns={[
              { key: "id", label: "작업", render: (t) => <Link to={`/proposals/${t.task_id}`}>{t.task_id.slice(0, 13)}</Link> },
              { key: "robot", label: "로봇", render: (t) => t.target_robot_id },
              { key: "ins", label: "지시", render: (t) => t.instruction },
              { key: "resume", label: "", render: (t) => (
                <button className="compact-button" disabled={!operator || busy}
                        onClick={() => resume(t.task_id)}
                        title={operator ? "RESUME 안전 제안을 생성합니다" : "OPERATOR 또는 ADMIN 권한이 필요합니다"}>
                  RESUME 제안 생성
                </button>
              ) },
            ]} />
          )}
        </Async>
      </Panel>
      <Panel title="정지 기록">
        <Table rows={stops} rowKey={(l) => l.log_id} empty="기록 없음" columns={[
          { key: "at", label: "시각", render: (l) => time(l.timestamp) },
          { key: "robot", label: "로봇", render: (l) => l.robot_id },
          { key: "by", label: "발원", render: (l) => l.triggered_by ?? "-" },
          { key: "msg", label: "내용", render: (l) => l.result_message },
        ]} />
      </Panel>
    </>
  );
}
