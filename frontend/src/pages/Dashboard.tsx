import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { ActionProposal, HazardEvent, Robot, TaskOrder } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Stat, Status, Table, countBy, summary, time } from "../components/ui";
import { SiteMap } from "./SiteMap";

// 지도와 목록이 같이 강조할 대상
type Focus = { robotId?: string; zoneId?: string };

const ACTIVE_TASK = ["CREATED", "PLANNING", "RUNNING", "HELD"];
const SEVERITY_ORDER = { CRITICAL: 0, WARNING: 1 } as const;

/** 진행 중 작업의 제안을 모은다. 전체 제안 목록 API가 없어 작업별로 조회한다. */
async function activeProposals(): Promise<ActionProposal[]> {
  const tasks = await api.tasks();
  const lists = await Promise.all(tasks.filter((t) => ACTIVE_TASK.includes(t.status)).map((t) => api.taskProposals(t.task_id)));
  // 작업마다 가장 최근 제안만 본다. 이전에 거절된 제안은 이미 새 제안으로 대체됐다.
  return lists.flatMap((list) => [...list].sort((a, b) => b.proposed_at.localeCompare(a.proposed_at)).slice(0, 1));
}

type QueueItem = {
  key: string;
  level: "critical" | "warning" | "normal";
  title: string;
  detail: string;
  focus: Focus;
  actions: ReactNode;
};

function buildQueue(
  hazards: HazardEvent[], robots: Robot[], proposals: ActionProposal[], tasks: TaskOrder[],
  run: (fn: () => Promise<unknown>) => void, busy: boolean,
): QueueItem[] {
  const taskOf = new Map(tasks.map((t) => [t.task_id, t]));
  const zoneOf = (taskId: string) => {
    const d = taskOf.get(taskId)?.destination;
    return d?.type === "ZONE" ? d.zone_id : undefined;
  };
  const items: QueueItem[] = [];
  for (const h of [...hazards].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])) {
    items.push({
      key: `h-${h.hazard_id}`,
      level: h.severity === "CRITICAL" ? "critical" : "warning",
      title: `${h.severity} 위험 · ${h.hazard_type} · ${h.zone_id}`,
      detail: `현장 확인 후 해제 · 관련 로봇 ${h.related_robot_ids.join(", ") || "-"} · ${time(h.detected_at)} 감지`,
      focus: { zoneId: h.zone_id, robotId: h.related_robot_ids[0] },
      actions: <button className="compact-button" disabled={busy} onClick={() => run(() => api.resolveHazard(h.hazard_id))}>운영자 해제</button>,
    });
  }
  for (const r of robots.filter((r) => r.status === "ESTOP" || r.status === "HOLD")) {
    items.push({
      key: `r-${r.robot_id}`,
      level: r.status === "ESTOP" ? "critical" : "warning",
      title: `${r.robot_id} ${r.status === "ESTOP" ? "비상정지" : "보류"}`,
      detail: "위험 해제 후 복귀 절차를 진행하세요",
      focus: { robotId: r.robot_id },
      actions: <Link className="button compact-button" to="/estop">복귀 절차</Link>,
    });
  }
  for (const p of proposals) {
    const base = { focus: { robotId: p.robot_id, zoneId: zoneOf(p.task_id) }, level: "normal" as const };
    const instruction = taskOf.get(p.task_id)?.instruction ?? p.task_id;
    if (p.status === "APPROVED") {
      items.push({ ...base, key: `p-${p.proposal_id}`, title: `실행 대기 · ${p.robot_id}`, detail: `안전 검증 통과 · ${instruction}`,
        actions: <><Link className="button compact-button" to={`/proposals/${p.task_id}`}>검토</Link>
          <button className="compact-button primary" disabled={busy} onClick={() => run(() => api.execute(p.proposal_id))}>실행</button></> });
    } else if (p.status === "PROPOSED") {
      items.push({ ...base, key: `p-${p.proposal_id}`, title: `검증 대기 · ${p.robot_id}`, detail: instruction,
        actions: <button className="compact-button" disabled={busy} onClick={() => run(() => api.evaluate(p.proposal_id))}>안전 검증</button> });
    } else if (p.status === "REJECTED") {
      items.push({ ...base, key: `p-${p.proposal_id}`, title: `검증 거절 · ${p.robot_id}`, detail: `거절 사유 확인 후 다시 제안 · ${instruction}`,
        actions: <Link className="button compact-button" to={`/proposals/${p.task_id}`}>사유 보기</Link> });
    }
  }
  return items;
}

export function Dashboard() {
  const robots = usePolling(api.robots);
  const tasks = usePolling(() => api.tasks());
  const hazards = usePolling(api.hazards);
  const proposals = usePolling(activeProposals, 2000);
  const logs = usePolling(api.executionLogs, 3000);
  const [focus, setFocus] = useState<Focus>({});
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();

  const openHazards = (hazards.data ?? []).filter((h) => h.status !== "RESOLVED");
  const estopped = (robots.data ?? []).filter((r) => r.status === "ESTOP" || r.status === "HOLD");

  const run = (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(undefined);
    fn().catch((e) => setActionError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e)))
      .finally(() => { setBusy(false); hazards.refresh(); robots.refresh(); proposals.refresh(); tasks.refresh(); });
  };
  const queue = buildQueue(openHazards, robots.data ?? [], proposals.data ?? [], tasks.data ?? [], run, busy);
  const toggle = (next: Focus) =>
    setFocus((cur) => (cur.robotId === next.robotId && cur.zoneId === next.zoneId ? {} : next));
  const focused = Boolean(focus.robotId || focus.zoneId);
  // 원천 조회가 한 번도 끝나지 않았으면 "조치할 항목 없음"이라고 단정하지 않는다.
  const queueLoading = [robots, tasks, proposals].some((s) => s.data === undefined) || (hazards.data === undefined && !hazards.missing);

  return (
    <div className="dashboard-page">
      <div className="page-head">
        <div className="page-head-copy"><span className="eyebrow">운영 대시보드</span><h1>안전이 먼저 움직입니다.</h1><p>로봇의 행동을 실시간으로 이해하고, 위험을 미리 감지해 작업을 더 안전하게 만듭니다.</p></div>
        <Link className="button primary" to="/site">새 작업 요청</Link>
      </div>

      <Panel title="조치 필요" action={queue.length > 0 && <span className="stat-detail">{queue.length}건</span>}>
        {queue.length === 0
          ? (queueLoading
            ? <p className="empty-state loading-state">조치할 항목을 확인하는 중…</p>
            : <p className="queue-clear">지금 조치할 항목이 없습니다.{hazards.missing && " (위험 조회 API 대기)"}</p>)
          : (
            <ul className="action-queue">
              {queue.map((item) => {
                const selected = focused && item.focus.robotId === focus.robotId && item.focus.zoneId === focus.zoneId;
                return (
                  <li key={item.key} data-level={item.level} className={`pickable${selected ? " selected" : ""}`}
                      onClick={(e) => { if (!(e.target as HTMLElement).closest("a,button")) toggle(item.focus); }}>
                    <span className="level" />
                    <div><strong>{item.title}</strong><small>{item.detail}</small></div>
                    <div className="actions">{item.actions}</div>
                  </li>
                );
              })}
            </ul>
          )}
        {actionError && <p className="error">{actionError}</p>}
      </Panel>

      <section className="dashboard-stage" aria-label="실시간 현장 지도">
        <SiteMap selectedRobotId={focus.robotId} selectedZoneId={focus.zoneId}
                 onRobotClick={(id) => toggle({ robotId: id })} onZoneClick={(id) => toggle({ zoneId: id })} />
        {focused && (
          <div className="focus-bar">
            <span>선택: {[focus.robotId, focus.zoneId].filter(Boolean).join(" · ")} — 아래 목록에서 관련 항목을 강조합니다.</span>
            <button className="compact-button" onClick={() => setFocus({})}>선택 해제</button>
          </div>
        )}
      </section>

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
                <Table rows={openHazards} rowKey={(h) => h.hazard_id} empty="열린 위험 없음"
                       onRowClick={(h) => toggle({ zoneId: h.zone_id, robotId: h.related_robot_ids[0] })}
                       isSelected={(h) => focused && (h.zone_id === focus.zoneId || (!!focus.robotId && h.related_robot_ids.includes(focus.robotId)))} columns={[
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
                <Table rows={rows} rowKey={(r) => r.robot_id} empty="등록된 로봇 없음"
                       onRowClick={(r) => toggle({ robotId: r.robot_id })} isSelected={(r) => r.robot_id === focus.robotId} columns={[
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
                       rowKey={(t) => t.task_id} empty="진행 중인 작업 없음"
                       onRowClick={(t) => toggle({ robotId: t.target_robot_id, zoneId: t.destination.type === "ZONE" ? t.destination.zone_id : undefined })}
                       isSelected={(t) => t.target_robot_id === focus.robotId} columns={[
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
                <Table rows={[...rows].reverse().slice(0, 5)} rowKey={(l) => l.log_id} empty="기록 없음"
                       onRowClick={(l) => toggle({ robotId: l.robot_id })} isSelected={(l) => l.robot_id === focus.robotId} columns={[
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
