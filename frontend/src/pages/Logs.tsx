import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";

export function Logs() {
  const logs = usePolling(api.executionLogs, 2000);
  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">판단 이력</span><h1>모든 판단을, 남깁니다.</h1><p>안전 판정과 실행 결과를 빠짐없이 시간순으로 추적합니다.</p></div></div>
      <Panel title="ExecutionLog">
        <Async state={logs}>
          {(rows) => (
            <Table rows={[...rows].reverse()} rowKey={(l) => l.log_id} empty="기록 없음" columns={[
              { key: "at", label: "시각", render: (l) => time(l.timestamp) },
              { key: "robot", label: "로봇", render: (l) => l.robot_id },
              { key: "proposal", label: "제안", render: (l) => l.proposal_id.slice(0, 17) },
              { key: "dec", label: "결정", render: (l) => <Status value={l.decision} /> },
              { key: "rules", label: "위반 규칙", render: (l) => l.rule_results.filter((r) => !r.passed).map((r) => r.rule_id).join(", ") || "-" },
              { key: "ms", label: "판정(ms)", render: (l) => l.evaluated_in_ms?.toFixed(1) ?? "-" },
              { key: "msg", label: "내용", render: (l) => l.result_message },
            ]} />
          )}
        </Async>
      </Panel>
    </>
  );
}
