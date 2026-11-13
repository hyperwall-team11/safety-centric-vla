import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { explainTaskError, type TaskErrorView } from "../api/taskError";
import type { TaskOrderPreview } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { ProposalReview, latestLog, planOverlay } from "../components/ProposalReview";
import { Panel } from "../components/ui";
import { CctvView } from "../components/CctvView";
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
 * 현장 화면: 지도·CCTV 아래에서 바로 작업을 요청한다.
 * 흐름: (현장 지도에서 로봇·구역 선택 또는) 자연어 입력 → 해석 미리보기 → 작업 생성 + 행동 제안
 * → 같은 화면에서 안전 검증(위반 위치를 지도에 표시) → 승인 시 실행.
 */
export function SiteWork() {
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<TaskOrderPreview>();
  const [error, setError] = useState<TaskErrorView>();
  const [busy, setBusy] = useState(false);
  const [pickedRobot, setPickedRobot] = useState<string>();
  const [pickedZone, setPickedZone] = useState<string>();
  const [taskId, setTaskId] = useState<string>();

  const task = usePolling(() => (taskId ? api.task(taskId) : noop()), 2000);
  const proposals = usePolling(() => (taskId ? api.taskProposals(taskId) : noop()), 2000);
  const logs = usePolling(() => (taskId ? api.executionLogs() : noop()), 2000);
  // 실시간 모드에서는 상태 변경 때만 다시 조회하므로, 작업이 바뀌면 직접 한 번 조회한다
  // (작업 생성 직후의 변경 알림이 taskId 반영보다 먼저 올 수 있다).
  const { refresh: refreshTask } = task;
  const { refresh: refreshProposals } = proposals;
  const { refresh: refreshLogs } = logs;
  useEffect(() => { refreshTask(); refreshProposals(); refreshLogs(); }, [taskId, refreshTask, refreshProposals, refreshLogs]);
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
      setError(e instanceof ApiError
        ? explainTaskError(e.detail)
        : { title: "연결을 확인해 주세요", message: String(e) });
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
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">현장 · 작업 요청</span><h1>현장을 보며 작업을 요청합니다.</h1><p>지도에서 로봇과 구역을 고르거나 지시를 입력하면, 안전 규칙으로 검증한 뒤 실행합니다.</p></div></div>
      <div className="columns site-top">
        <Panel title="현장 지도" className="map-panel">
          <SiteMap
                   selectedRobotId={proposal?.robot_id ?? preview?.target_robot_id ?? pickedRobot}
                   selectedZoneId={destZone ?? pickedZone}
                   onRobotClick={taskId ? undefined : (id) => pick(id, pickedZone)}
                   onZoneClick={taskId ? undefined : (id) => pick(pickedRobot, id)}
                   plan={overlay.plan} violation={overlay.violation} />
          <p className="map-hint">
            {taskId ? "선은 제안된 이동, 빨간 구역·작업자는 안전 검증에서 위반 원인으로 지목된 곳입니다."
                    : "로봇과 구역을 누르면 아래 명령이 채워집니다."}
          </p>
        </Panel>
        <Panel title="CCTV" className="camera-panel">
          <CctvView />
        </Panel>
      </div>

      <section id="request" className="request-card" aria-label="작업 요청">
        <header className="request-card-head">
          <h2>작업 요청</h2>
          <ol className="request-steps" aria-label="진행 단계">
            <li className={step === 1 ? "current" : "done"}>명령</li>
            <li className={step === 2 ? "current" : step > 2 ? "done" : ""}>해석 확인</li>
            <li className={step === 3 ? "current" : ""}>안전 검증·실행</li>
          </ol>
        </header>

        <form className="command-bar" onSubmit={(e) => { e.preventDefault(); if (instruction.trim()) run(async () => setPreview(await api.previewTask(instruction))); }}>
          <input value={instruction} disabled={Boolean(taskId)} onChange={(e) => { setInstruction(e.target.value); setPreview(undefined); }}
                 placeholder="지도에서 로봇과 구역을 누르거나, 예: robot-01 로봇을 A구역으로 이동해" aria-label="자연어 명령" />
          {taskId
            ? <button type="button" onClick={reset}>새 요청</button>
            : <button className="primary" disabled={busy || !instruction.trim()}>명령 해석</button>}
        </form>
        {error && (
          <div className="error task-error" role="alert">
            <strong>{error.title}</strong>
            <p>{error.message}</p>
            {error.hint && <small>{error.hint}</small>}
          </div>
        )}

        {preview && (
          <div className="request-block">
            <h3>해석 결과</h3>
            <dl className="chips">
              <div><dt>로봇</dt><dd>{preview.target_robot_id}</dd></div>
              <div><dt>행동</dt><dd>{preview.action_type}</dd></div>
              <div><dt>목적지</dt><dd>{destination(preview)}</dd></div>
              {preview.target_object_id && <div><dt>대상 물체</dt><dd>{preview.target_object_id}</dd></div>}
            </dl>
            {!taskId && (
              <button className="primary" disabled={busy} onClick={() => run(async () => {
                const created = await api.createTask(instruction);
                await api.createProposal(created.task_id);
                setTaskId(created.task_id);
              })}>이대로 작업 만들고 안전 검증 받기</button>
            )}
          </div>
        )}

        {taskId && (
          <div className="request-block">
            <h3>안전 검증·실행 <Link to={`/proposals/${taskId}`}>제안 이력</Link></h3>
            {proposal
              ? <ProposalReview proposal={proposal} log={log} busy={busy} act={run} />
              : <p className="empty-state loading-state">행동 제안을 불러오는 중…</p>}
          </div>
        )}
      </section>
    </>
  );
}
