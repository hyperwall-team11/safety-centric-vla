import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { TaskOrderPreview } from "../api/types";
import { Panel } from "../components/ui";

const destination = (p: TaskOrderPreview) =>
  p.destination.type === "ZONE"
    ? `구역 ${p.destination.zone_id}`
    : `좌표 (${p.destination.position.x}, ${p.destination.position.y}, ${p.destination.position.z})`;

/** 흐름: 자연어 입력 → 해석 미리보기 → 작업 생성 + 행동 제안 생성 → 제안 화면으로 이동 */
export function TaskRequest() {
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<TaskOrderPreview>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(undefined);
    try { await fn(); } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e));
    } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head"><h1>작업 요청</h1></div>
      <Panel title="1. 명령 입력">
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (instruction.trim()) run(async () => setPreview(await api.previewTask(instruction))); }}>
          <input value={instruction} onChange={(e) => { setInstruction(e.target.value); setPreview(undefined); }}
                 placeholder="robot-01 로봇을 A구역으로 이동해" aria-label="자연어 명령" />
          <button disabled={busy || !instruction.trim()}>해석</button>
        </form>
        {error && <p className="error">{error}</p>}
      </Panel>
      <Panel title="2. 해석 확인">
        {preview ? (
          <>
            <dl className="fields">
              <dt>로봇</dt><dd>{preview.target_robot_id}</dd>
              <dt>행동</dt><dd>{preview.action_type}</dd>
              <dt>목적지</dt><dd>{destination(preview)}</dd>
              <dt>대상 물체</dt><dd>{preview.target_object_id ?? "-"}</dd>
            </dl>
            <button disabled={busy} onClick={() => run(async () => {
              const task = await api.createTask(instruction);
              await api.createProposal(task.task_id);
              navigate(`/proposals/${task.task_id}`);
            })}>작업 생성하고 행동 제안 받기</button>
          </>
        ) : <p className="note">명령을 해석하면 여기에 표시돼요.</p>}
      </Panel>
    </>
  );
}
