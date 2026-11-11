import { Link } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";

/** 흐름: 정지(ESTOP·HOLD) 확인 → 위험 해제 → RESUME 행동 제안 → 검증·실행. 해제만으로 로봇이 움직이지 않는다. */
export function EStop() {
  const robots = usePolling(api.robots);
  const tasks = usePolling(() => api.tasks("HELD"), 2000);
  const logs = usePolling(api.executionLogs, 2000);
  const stops = (logs.data ?? []).filter((l) => l.decision === "ESTOP").reverse();

  return (
    <>
      <div className="page-head">
        <div className="page-head-copy"><span className="eyebrow">비상정지 · 복귀</span><h1>복귀는,<br />한 단계씩.</h1><p>정지 상태를 확인하고 안전한 복귀 절차를 순서대로 진행합니다.</p></div>
        <button className="danger" disabled title="운영자 E-Stop API가 아직 없어요">비상정지 · API 대기</button>
      </div>
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
          <li>현장 확인 후 <Link to="/hazards">위험 모니터링</Link>에서 관련 위험을 운영자 해제</li>
          <li>보류 작업에서 RESUME 행동 제안을 만들고 검증</li>
          <li>승인되면 실행. 로봇이 IDLE로 돌아오면 원래 작업을 다시 요청</li>
        </ol>
      </Panel>
      <Panel title="보류 작업">
        <Async state={tasks}>
          {(rows) => (
            <Table rows={rows} rowKey={(t) => t.task_id} empty="보류 작업 없음" columns={[
              { key: "id", label: "작업", render: (t) => <Link to={`/proposals/${t.task_id}`}>{t.task_id.slice(0, 13)}</Link> },
              { key: "robot", label: "로봇", render: (t) => t.target_robot_id },
              { key: "ins", label: "지시", render: (t) => t.instruction },
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
