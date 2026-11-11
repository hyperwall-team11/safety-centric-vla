import { Link } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Stat, Status, Table, countBy, summary, time } from "../components/ui";
import { SiteMap } from "./SiteMap";

export function Dashboard() {
  const robots = usePolling(api.robots);
  const tasks = usePolling(() => api.tasks());
  const hazards = usePolling(api.hazards);
  const logs = usePolling(api.executionLogs, 3000);

  const openHazards = (hazards.data ?? []).filter((h) => h.status !== "RESOLVED");
  const estopped = (robots.data ?? []).filter((r) => r.status === "ESTOP" || r.status === "HOLD");

  return (
    <div className="dashboard-page">
      <div className="page-head">
        <div className="page-head-copy"><span className="eyebrow">운영 대시보드</span><h1>안전이 먼저 움직입니다.</h1><p>로봇의 행동을 실시간으로 이해하고, 위험을 미리 감지해 작업을 더 안전하게 만듭니다.</p></div>
        <Link className="button primary" to="/tasks/new">새 작업 요청</Link>
      </div>

      <section className="dashboard-stage" aria-label="실시간 현장 지도"><SiteMap compact /></section>

      <div className="stats">
        <Stat label="운영 로봇" value={robots.data?.length ?? "-"} detail={summary(countBy(robots.data ?? [], (r) => r.status))} />
        <Stat label="작업 진행" value={tasks.data?.length ?? "-"} detail={summary(countBy(tasks.data ?? [], (t) => t.status))} />
        <Stat label="열린 위험" value={hazards.missing ? "API 대기" : openHazards.length}
              detail={summary(countBy(openHazards, (h) => h.severity))} />
        <Stat label="비상정지" value={estopped.length} detail={estopped.map((r) => r.robot_id).join(", ") || "-"} />
        <Stat label="누적 판단" value={logs.data?.length ?? "-"} detail={summary(countBy(logs.data ?? [], (l) => l.decision))} />
      </div>

      <div className="columns">
        <div className="stack">
          <Panel title="열린 위험" action={<Link to="/hazards">위험 모니터링</Link>}>
            <Async state={hazards}>
              {() => (
                <Table rows={openHazards} rowKey={(h) => h.hazard_id} empty="열린 위험 없음" columns={[
                  { key: "type", label: "유형", render: (h) => h.hazard_type },
                  { key: "zone", label: "구역", render: (h) => h.zone_id },
                  { key: "robots", label: "관련 로봇", render: (h) => h.related_robot_ids.join(", ") || "-" },
                  { key: "at", label: "감지", render: (h) => time(h.detected_at) },
                  { key: "sev", label: "등급", render: (h) => <Status value={h.severity} /> },
                ]} />
              )}
            </Async>
          </Panel>
        </div>
        <div className="stack">
          <Panel title="로봇 상태">
            <Async state={robots}>
              {(rows) => (
                <Table rows={rows} rowKey={(r) => r.robot_id} empty="등록된 로봇 없음" columns={[
                  { key: "id", label: "로봇", render: (r) => r.robot_id },
                  { key: "type", label: "종류", render: (r) => r.robot_type },
                  { key: "status", label: "상태", render: (r) => <Status value={r.status} /> },
                  { key: "hb", label: "하트비트", render: (r) => time(r.last_heartbeat_at) },
                ]} />
              )}
            </Async>
          </Panel>
          <Panel title="진행 중·보류 작업" action={<Link to="/proposals">행동 제안</Link>}>
            <Async state={tasks}>
              {(rows) => (
                <Table rows={rows.filter((t) => ["CREATED", "PLANNING", "RUNNING", "HELD"].includes(t.status))}
                       rowKey={(t) => t.task_id} empty="진행 중인 작업 없음" columns={[
                  { key: "id", label: "작업", render: (t) => <Link to={`/proposals/${t.task_id}`}>{t.task_id.slice(0, 13)}</Link> },
                  { key: "robot", label: "로봇", render: (t) => t.target_robot_id },
                  { key: "ins", label: "지시", render: (t) => t.instruction },
                  { key: "status", label: "상태", render: (t) => <Status value={t.status} /> },
                ]} />
              )}
            </Async>
          </Panel>
          <Panel title="최근 판단" action={<Link to="/logs">판단 이력</Link>}>
            <Async state={logs}>
              {(rows) => (
                <Table rows={[...rows].reverse().slice(0, 5)} rowKey={(l) => l.log_id} empty="기록 없음" columns={[
                  { key: "at", label: "시각", render: (l) => time(l.timestamp) },
                  { key: "robot", label: "로봇", render: (l) => l.robot_id },
                  { key: "dec", label: "결정", render: (l) => <Status value={l.decision} /> },
                  { key: "msg", label: "내용", render: (l) => l.result_message },
                ]} />
              )}
            </Async>
          </Panel>
        </div>
      </div>
    </div>
  );
}
