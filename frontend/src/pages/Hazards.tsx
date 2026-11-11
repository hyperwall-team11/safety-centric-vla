import { useState } from "react";
import { ApiError, api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";
import type { HazardEvent } from "../api/types";

/** 흐름: CV가 등록한 위험 확인 → 현장 확인 → [운영자 해제]. CRITICAL은 운영자만 해제할 수 있다. */
export function Hazards() {
  const hazards = usePolling(api.hazards);
  const [filter, setFilter] = useState<"OPEN" | "ALL">("OPEN");
  const [error, setError] = useState<string>();

  const resolve = (h: HazardEvent) => {
    if (!window.confirm(`${h.hazard_id}를 해제할까요? 현장을 확인한 뒤 해제하세요.`)) return;
    setError(undefined);
    api.resolveHazard(h.hazard_id)
      .catch((e) => setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e)))
      .finally(hazards.refresh);
  };

  return (
    <>
      <div className="page-head">
        <h1>위험 모니터링</h1>
        <select value={filter} onChange={(e) => setFilter(e.target.value as "OPEN" | "ALL")} aria-label="상태 필터">
          <option value="OPEN">열린 위험</option>
          <option value="ALL">전체</option>
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      {(["CRITICAL", "WARNING"] as const).map((severity) => (
        <Panel key={severity} title={`${severity} 위험`}>
          <Async state={hazards}>
            {(rows) => (
              <Table rows={rows.filter((h) => h.severity === severity && (filter === "ALL" || h.status !== "RESOLVED"))}
                     rowKey={(h) => h.hazard_id} empty="없음" columns={[
                { key: "type", label: "유형", render: (h) => h.hazard_type },
                { key: "zone", label: "구역", render: (h) => h.zone_id },
                { key: "robots", label: "관련 로봇", render: (h) => h.related_robot_ids.join(", ") || "-" },
                { key: "obs", label: "근거 관측", render: (h) => h.source_observation_ids.length },
                { key: "conf", label: "신뢰도", render: (h) => h.confidence.toFixed(2) },
                { key: "at", label: "감지", render: (h) => time(h.detected_at) },
                { key: "status", label: "상태", render: (h) => <Status value={h.status} /> },
                { key: "act", label: "", render: (h) => h.status !== "RESOLVED" && <button onClick={() => resolve(h)}>운영자 해제</button> },
              ]} />
            )}
          </Async>
        </Panel>
      ))}
    </>
  );
}
