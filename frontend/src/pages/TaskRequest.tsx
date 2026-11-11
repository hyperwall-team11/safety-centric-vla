import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { TaskOrderPreview } from "../api/types";
import { Panel } from "../components/ui";
import { SiteMap } from "./SiteMap";

const destination = (p: TaskOrderPreview) =>
  p.destination.type === "ZONE"
    ? `구역 ${p.destination.zone_id}`
    : `좌표 (${p.destination.position.x}, ${p.destination.position.y}, ${p.destination.position.z})`;

// task_parser.py가 읽는 형식: "<robot_id> 로봇을" + 따옴표 구역명 "<zone_id>"으로
const compose = (robotId?: string, zoneId?: string) =>
  `${robotId ? `${robotId} 로봇을 ` : ""}${zoneId ? `"${zoneId}"으로 ` : ""}이동해`;

/** 흐름: (현장 지도에서 로봇·구역 선택 또는) 자연어 입력 → 해석 미리보기 → 작업 생성 + 행동 제안 생성 → 제안 화면으로 이동 */
export function TaskRequest() {
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<TaskOrderPreview>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [pickedRobot, setPickedRobot] = useState<string>();
  const [pickedZone, setPickedZone] = useState<string>();
  const navigate = useNavigate();

  const pick = (robotId?: string, zoneId?: string) => {
    setPickedRobot(robotId);
    setPickedZone(zoneId);
    setInstruction(compose(robotId, zoneId));
    setPreview(undefined);
  };
  const destZone = preview?.destination.type === "ZONE" ? preview.destination.zone_id : undefined;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try { await fn(); } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">작업 요청</span><h1>말하면, 안전하게<br />계획합니다.</h1><p>자연어 지시를 이해하고 실행 계획을 제안한 뒤, 안전 규칙에 따라 검증합니다.</p></div></div>
      <div className="stepper" aria-label="작업 요청 단계"><span className="active"><b>01</b> 명령 입력</span><span className={preview ? "active" : ""}><b>02</b> 해석 확인</span><span><b>03</b> 안전 검증</span></div>
      <div className="request-layout">
      <Panel title="현장 지도" className="map-panel">
        <SiteMap compact
                 selectedRobotId={preview?.target_robot_id ?? pickedRobot}
                 selectedZoneId={destZone ?? pickedZone}
                 onRobotClick={(id) => pick(id, pickedZone)}
                 onZoneClick={(id) => pick(pickedRobot, id)} />
        <p className="map-hint">로봇과 구역을 누르면 명령이 채워집니다. 직접 고쳐 써도 됩니다.</p>
      </Panel>
      <div className="stack">
      <Panel title="명령 입력" className="request-panel">
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (instruction.trim()) run(async () => setPreview(await api.previewTask(instruction))); }}>
          <input value={instruction} onChange={(e) => { setInstruction(e.target.value); setPreview(undefined); }}
                 placeholder="robot-01 로봇을 A구역으로 이동해" aria-label="자연어 명령" />
          <button className="primary" disabled={busy || !instruction.trim()}>명령 해석</button>
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
            <button className="primary" disabled={busy} onClick={() => run(async () => {
              const task = await api.createTask(instruction);
              await api.createProposal(task.task_id);
              navigate(`/proposals/${task.task_id}`);
            })}>작업 생성 후 행동 제안 받기</button>
          </>
        ) : <p className="empty-state roomy">명령을 해석하면 로봇, 행동, 목적지를 구조화해 표시합니다.</p>}
      </Panel>
      </div>
      </div>
    </>
  );
}
