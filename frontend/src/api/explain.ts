import type { ActionProposal, RuleResult } from "./types";

// backend/app/safety.py의 rule_id와 사유 문구를 운영자용 설명·해결 방법으로 바꾼다.
// 사유 문구 형식이 바뀌면 여기의 정규식도 함께 고친다.

export type RuleExplain = {
  label: string;
  /** 위반일 때 운영자가 할 일 */
  hint?: string;
  /** 지도에 위반 위치로 표시할 구역·작업자 */
  zoneIds: string[];
  workerIds: string[];
  /** 속도 위반이면 허용 속도(m/s). 이 값으로 다시 제안할 수 있다. */
  allowedSpeed?: number;
  link?: { to: string; label: string };
};

const LABELS: Record<string, string> = {
  "precondition-robot-state": "로봇 상태",
  "precondition-control-adapter": "제어 어댑터",
  "rule-speed-limit": "속도 상한 (SR-01)",
  "rule-keep-out": "금지구역 경로 (SR-02)",
  "rule-safety-distance": "작업자 안전거리 (SR-03)",
  "rule-critical-hazard": "CRITICAL 위험 (SR-04)",
  "rule-observation-gap": "센서 관측 공백 (SR-06)",
};

export function explainRule(r: RuleResult): RuleExplain {
  const base: RuleExplain = { label: LABELS[r.rule_id] ?? r.rule_id, zoneIds: [], workerIds: [] };
  if (r.passed) return base;
  switch (r.rule_id) {
    case "precondition-robot-state":
      return { ...base, hint: "로봇이 IDLE이 될 때까지 기다리거나, 정지 상태면 복귀 절차를 먼저 진행하세요.", link: { to: "/estop", label: "복귀 절차" } };
    case "precondition-control-adapter":
      return { ...base, hint: "로봇의 제어 어댑터 연결을 확인하세요." };
    case "rule-speed-limit": {
      const allowed = Number(/허용 속도 ([\d.]+)m\/s/.exec(r.reason)?.[1]);
      return { ...base, allowedSpeed: Number.isFinite(allowed) ? allowed : undefined, hint: "허용 속도 이하로 다시 제안하세요." };
    }
    case "rule-keep-out": {
      if (/경로가 없음/.test(r.reason)) {
        return { ...base, hint: "제안에 이동 경로(trajectory)가 없어 금지구역 통과 여부를 확인할 수 없습니다. 경로를 포함한 제안이 필요합니다." };
      }
      const zone = /^(\S+) KEEP_OUT/.exec(r.reason)?.[1];
      return { ...base, zoneIds: zone ? [zone] : [], hint: "지도에 표시된 금지구역을 지나지 않는 목적지로 다시 요청하세요." };
    }
    case "rule-safety-distance": {
      const workers = [...r.reason.matchAll(/([\w-]+): [\d.]+m/g)].map((m) => m[1]);
      return { ...base, workerIds: workers, hint: "작업자가 안전거리 밖으로 벗어난 뒤 다시 검증하세요." };
    }
    case "rule-critical-hazard":
      return { ...base, hint: "관련 CRITICAL 위험을 현장에서 확인하고 해제한 뒤 다시 검증하세요.", link: { to: "/hazards", label: "위험 보기" } };
    case "rule-observation-gap":
      return { ...base, hint: "목적지 구역을 비추는 센서가 ONLINE인지 확인하세요." };
    default:
      return base;
  }
}

export type Point = { x: number; y: number };

/** ActionProposal.trajectory → 지도 좌표 목록. 형식: [{ position: { x, y } }, ...] */
export function trajectoryPoints(p: ActionProposal | undefined): Point[] {
  return (p?.trajectory ?? []).flatMap((w) => {
    const pos = (w as { position?: Partial<Point> }).position;
    return typeof pos?.x === "number" && typeof pos?.y === "number" ? [{ x: pos.x, y: pos.y }] : [];
  });
}
