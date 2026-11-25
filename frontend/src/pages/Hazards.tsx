import { useState } from "react";
import { ApiError, api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Async, Panel, Status, Table, time } from "../components/ui";
import type { HazardEvent } from "../api/types";

/** 흐름: CV가 등록한 위험 확인 → 현장 확인 → [운영자 해제]. CRITICAL은 운영자만 해제할 수 있다. */
export function Hazards() {
  const hazards = usePolling(api.hazards);
  const auth = usePolling(api.authStatus, 5000, false);
  const [filter, setFilter] = useState<"OPEN" | "ALL">("OPEN");
  const [error, setError] = useState<string>();

  const resolve = (h: HazardEvent) => {
    if (!window.confirm(`${h.hazard_id}를 해제할까요? 현장을 확인한 뒤 해제하세요.`)) return;
    setError(undefined);
    api.resolveHazard(h.hazard_id)
      .catch((e) => setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e)))
      .finally(hazards.refresh);
  };
  const operator = auth.data?.enabled === false || ["OPERATOR", "ADMIN"].includes(auth.data?.role ?? "");

  const action = (hazard: HazardEvent) => {
    if (hazard.status === "RESOLVED") return null;
    const cvPending = hazard.severity === "CRITICAL"
      && hazard.hazard_type === "WORKER_INTRUSION"
      && hazard.status === "OPEN";
    if (cvPending) return <button className="compact-button" disabled>CV 안전 확인 대기</button>;
    return (
      <button className="compact-button" disabled={!operator} onClick={() => resolve(hazard)}
              title={operator ? "현장 확인 후 위험을 해제합니다" : "OPERATOR 또는 ADMIN 권한이 필요합니다"}>
        관리자 해제
      </button>
    );
  };

  return (
    <>
      <div className="page-head">
        <div className="page-head-copy"><span className="eyebrow">위험 모니터링</span><h1>CV 확인 뒤, 관리자가 해제합니다.</h1><p>CRITICAL 작업자 침입은 모든 CCTV가 2초간 안전을 확인해도 차단을 유지합니다. 관리자 승인과 RESUME 검증까지 끝나야 로봇이 복귀합니다.</p></div>
        <select value={filter} onChange={(e) => setFilter(e.target.value as "OPEN" | "ALL")} aria-label="상태 필터">
          <option value="OPEN">열린 위험</option>
          <option value="ALL">전체</option>
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      {(["CRITICAL", "WARNING"] as const).map((severity) => (
        <Panel key={severity} className={`hazard-panel ${severity.toLowerCase()}`} title={`${severity} 위험`}>
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
                { key: "clear", label: "복귀 게이트", render: (h) => h.status === "OPEN" ? "위험 관측 중" : h.status === "ACKNOWLEDGED" ? "CV 안전 확인 · 승인 대기" : "관리자 해제 완료" },
                { key: "act", label: "", render: action },
              ]} />
            )}
          </Async>
        </Panel>
      ))}
    </>
  );
}
