import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { TaskOrderPreview } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { ProposalReview, latestLog, planOverlay } from "../components/ProposalReview";
import { Panel } from "../components/ui";
import { SiteMap } from "./SiteMap";

const destination = (p: TaskOrderPreview) =>
  p.destination.type === "ZONE"
    ? `구역 ${p.destination.zone_id}`
    : `좌표 (${p.destination.position.x}, ${p.destination.position.y}, ${p.destination.position.z})`;

// task_parser.py가 읽는 형식: "<robot_id> 로봇을" + 따옴표 구역명 "<zone_id>"으로
const compose = (robotId?: string, zoneId?: string) =>
  `${robotId ? `${robotId} 로봇을 ` : ""}${zoneId ? `"${zoneId}"으로 ` : ""}이동해`;

const noop = () => Promise.resolve(undefined);

/**
 * 흐름: (현장 지도에서 로봇·구역 선택 또는) 자연어 입력 → 해석 미리보기 → 작업 생성 + 행동 제안
 * → 같은 화면에서 안전 검증(위반 위치를 지도에 표시) → 승인 시 실행.
 */
export function TaskRequest() {
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<TaskOrderPreview>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [pickedRobot, setPickedRobot] = useState<string>();
  const [pickedZone, setPickedZone] = useState<string>();
  const [taskId, setTaskId] = useState<string>();

  const task = usePolling(() => (taskId ? api.task(taskId) : noop()), 2000);
  const proposals = usePolling(() => (taskId ? api.taskProposals(taskId) : noop()), 2000);
  const logs = usePolling(() => (taskId ? api.executionLogs() : noop()), 2000);
  const proposal = [...(proposals.data ?? [])].sort((a, b) => b.proposed_at.localeCompare(a.proposed_at))[0];
  const log = latestLog(logs.data, proposal?.proposal_id);
  const overlay = planOverlay(task.data, proposal, log);

  const pick = (robotId?: string, zoneId?: string) => {
    if (taskId) return; // 진행 중인 작업이 있으면 지도 선택으로 명령을 바꾸지 않는다.
    setPickedRobot(robotId);
    setPickedZone(zoneId);
    setInstruction(compose(robotId, zoneId));
    setPreview(undefined);
  };
  const destZone = preview?.destination.type === "ZONE" ? preview.destination.zone_id : undefined;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(undefined);
    try { await fn(); } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    } finally {
      setBusy(false);
      task.refresh(); proposals.refresh(); logs.refresh();
    }
  };
  const reset = () => {
    setTaskId(undefined); setPreview(undefined); setInstruction(""); setPickedRobot(undefined); setPickedZone(undefined); setError(undefined);
  };

  const step = taskId ? 3 : preview ? 2 : 1;

  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">작업 요청</span><h1>말하면, 안전하게 계획합니다.</h1><p>자연어 지시를 이해하고 실행 계획을 제안한 뒤, 안전 규칙에 따라 검증합니다.</p></div></div>
      <div className="stepper compact" aria-label="작업 요청 단계">
        <span className="active"><b>01</b> 명령 입력</span>
        <span className={step >= 2 ? "active" : ""}><b>02</b> 해석 확인</span>
        <span className={step >= 3 ? "active" : ""}><b>03</b> 안전 검증·실행</span>
      </div>
      <div className="request-layout">
        <Panel title="현장 지도" className="map-panel">
          <SiteMap compact
                   selectedRobotId={proposal?.robot_id ?? preview?.target_robot_id ?? pickedRobot}
                   selectedZoneId={destZone ?? pickedZone}
                   onRobotClick={taskId ? undefined : (id) => pick(id, pickedZone)}
                   onZoneClick={taskId ? undefined : (id) => pick(pickedRobot, id)}
                   plan={overlay.plan} violation={overlay.violation} />
          <p className="map-hint">
            {taskId ? "선은 제안된 이동, 빨간 구역·작업자는 안전 검증에서 위반 원인으로 지목된 곳입니다."
                    : "로봇과 구역을 누르면 명령이 채워집니다. 직접 고쳐 써도 됩니다."}
          </p>
        </Panel>
        <div className="stack">
          <Panel title="명령 입력" className="request-panel">
            <form className="row" onSubmit={(e) => { e.preventDefault(); if (instruction.trim()) run(async () => setPreview(await api.previewTask(instruction))); }}>
              <input value={instruction} disabled={Boolean(taskId)} onChange={(e) => { setInstruction(e.target.value); setPreview(undefined); }}
                     placeholder="robot-01 로봇을 A구역으로 이동해" aria-label="자연어 명령" />
              {taskId
                ? <button type="button" onClick={reset}>새 요청</button>
                : <button className="primary" disabled={busy || !instruction.trim()}>명령 해석</button>}
            </form>
            {error && <p className="error">{error}</p>}
          </Panel>
          <Panel title="해석 확인" className="request-panel">
            {preview ? (
              <>
                <dl className="fields">
                  <dt>로봇</dt><dd>{preview.target_robot_id}</dd>
                  <dt>행동</dt><dd>{preview.action_type}</dd>
                  <dt>목적지</dt><dd>{destination(preview)}</dd>
                  <dt>대상 물체</dt><dd>{preview.target_object_id ?? "-"}</dd>
                </dl>
                {!taskId && (
                  <button className="primary" disabled={busy} onClick={() => run(async () => {
                    const created = await api.createTask(instruction);
                    await api.createProposal(created.task_id);
                    setTaskId(created.task_id);
                  })}>작업 생성 후 행동 제안 받기</button>
                )}
              </>
            ) : <p className="empty-state roomy">명령을 해석하면 로봇, 행동, 목적지를 구조화해 표시합니다.</p>}
          </Panel>
          {taskId && (
            <Panel title="안전 검증·실행" className="request-panel" action={<Link to={`/proposals/${taskId}`}>제안 이력</Link>}>
              {proposal
                ? <ProposalReview proposal={proposal} log={log} busy={busy} act={run} />
                : <p className="empty-state loading-state">행동 제안을 불러오는 중…</p>}
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
