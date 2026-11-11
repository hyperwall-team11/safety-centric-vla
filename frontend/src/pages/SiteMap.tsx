import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import type { Zone } from "../api/types";
import type { Point } from "../api/explain";

function ring(zone: Zone): [number, number][] {
  const g = zone.geometry as { points?: number[][]; coordinates?: number[][][] };
  const pts = g.points ?? g.coordinates?.[0] ?? [];
  return pts.map((p) => [p[0], p[1]]);
}

/** 바닥 평면도(x 오른쪽, y 위). 구역·로봇·작업자·열린 위험·계획 경로를 그린다. */
type SiteMapProps = {
  /** 작업 요청 화면처럼 지도에서 대상을 고를 때 쓴다. */
  selectedRobotId?: string;
  selectedZoneId?: string;
  onRobotClick?: (robotId: string) => void;
  onZoneClick?: (zoneId: string) => void;
  /** 행동 제안의 이동 계획. path가 2점 미만이면 로봇→목적지 구역을 점선(경로 없음)으로 잇는다. */
  plan?: { robotId: string; path: Point[]; toZoneId?: string; status: "pending" | "passed" | "blocked" };
  /** 안전 검증에서 위반 원인으로 지목된 구역·작업자 */
  violation?: { zoneIds: string[]; workerIds: string[] };
};

const centroid = (pts: [number, number][]) => ({
  x: pts.reduce((a, p) => a + p[0], 0) / pts.length,
  y: pts.reduce((a, p) => a + p[1], 0) / pts.length,
});

export function SiteMap({ selectedRobotId, selectedZoneId, onRobotClick, onZoneClick, plan, violation }: SiteMapProps) {
  const zones = usePolling(api.zones, 5000);
  const robots = usePolling(api.robots);
  const workers = usePolling(api.workers);
  const hazards = usePolling(api.hazards);
  // 구역별 가장 높은 열린 위험 등급. HazardEvent에는 좌표가 없어 구역 단위로 표시한다.
  const hazardLevel = new Map<string, string>();
  for (const h of hazards.data ?? []) {
    if (h.status === "RESOLVED") continue;
    if (h.severity === "CRITICAL" || !hazardLevel.has(h.zone_id)) hazardLevel.set(h.zone_id, h.severity);
  }
  const isEmpty = (zones.data?.length ?? 0) === 0 && (robots.data?.length ?? 0) === 0;
  const view = { x: -4, y: -4, w: 8, h: 8 }; // 시나리오 장면 기준 범위(m)
  const flip = (y: number) => -y;

  // 계획 경로: trajectory가 있으면 그대로, 없으면 로봇 위치 → 목적지 구역 중심을 점선으로 잇는다.
  let planLine: { points: string; estimated: boolean; end?: Point } | undefined;
  if (plan) {
    let pts = plan.path;
    let estimated = false;
    if (pts.length < 2) {
      const robot = robots.data?.find((r) => r.robot_id === plan.robotId);
      const zone = zones.data?.find((z) => z.zone_id === plan.toZoneId);
      if (robot && zone && ring(zone).length > 0) {
        pts = [{ x: robot.pose.position.x, y: robot.pose.position.y }, centroid(ring(zone))];
        estimated = true;
      }
    }
    if (pts.length >= 2) planLine = { points: pts.map((p) => `${p.x},${flip(p.y)}`).join(" "), estimated, end: pts[pts.length - 1] };
  }

  const map = (
    <figure className={`map ${isEmpty ? "is-empty" : ""}`}>
      <svg viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`} role="img" aria-label="현장 지도">
        {isEmpty && (
          <g className="map-awaiting" aria-hidden="true">
            <path d="M-3 2.6 C-2 1.8,-1.6 1.9,-.8 1 C.3-.3,1.3.2,3-2" />
            <circle cx="-3" cy="2.6" r=".13" />
            <circle cx="3" cy="-2" r=".13" />
            <rect x="-.32" y=".55" width=".64" height=".64" rx=".12" />
          </g>
        )}
        {(zones.data ?? []).map((z) => (
          <g key={z.zone_id} className={`zone${onZoneClick ? " pickable" : ""}${z.zone_id === selectedZoneId ? " selected" : ""}`} data-type={z.zone_type} data-hazard={hazardLevel.get(z.zone_id)}
             data-violation={violation?.zoneIds.includes(z.zone_id) || undefined}
             onClick={onZoneClick && (() => onZoneClick(z.zone_id))}>
            <polygon points={ring(z).map(([x, y]) => `${x},${flip(y)}`).join(" ")} />
            {hazardLevel.has(z.zone_id) && ring(z).length > 0 && (() => {
              const { x: cx, y: cy } = centroid(ring(z));
              return <g className="hazard-marker"><circle cx={cx} cy={flip(cy)} r={0.32} /><text x={cx} y={flip(cy) + 0.07}>!</text></g>;
            })()}
            {ring(z)[0] && <text x={ring(z)[0][0]} y={flip(ring(z)[0][1]) - 0.1}>{z.zone_id}</text>}
          </g>
        ))}
        {planLine && (
          <g className="plan" data-status={plan?.status} data-estimated={planLine.estimated || undefined}>
            <polyline points={planLine.points} />
            {planLine.end && <circle cx={planLine.end.x} cy={flip(planLine.end.y)} r={0.1} />}
          </g>
        )}
        {(robots.data ?? []).map((r) => (
          <g key={r.robot_id} className={`robot${onRobotClick ? " pickable" : ""}${r.robot_id === selectedRobotId ? " selected" : ""}`}
             onClick={onRobotClick && ((e) => { e.stopPropagation(); onRobotClick(r.robot_id); })}>
            <rect x={r.pose.position.x - 0.25} y={flip(r.pose.position.y) - 0.25} width={0.5} height={0.5} />
            <text x={r.pose.position.x + 0.3} y={flip(r.pose.position.y)}>{r.robot_id}</text>
          </g>
        ))}
        {(workers.data?.workers ?? []).map((w) => (
          <circle key={w.worker_id} className={`worker${violation?.workerIds.includes(w.worker_id) ? " violation" : ""}`} cx={w.pose.position.x} cy={flip(w.pose.position.y)} r={0.2} />
        ))}
      </svg>
      <figcaption className="map-legend">
        <span><i className="legend-robot" /> 로봇</span><span><i className="legend-worker" /> 작업자</span><span><i className="legend-hazard" /> 열린 위험</span>{planLine && <span><i className="legend-plan" /> {planLine.estimated ? "예상 이동(경로 없음)" : "계획 경로"}</span>}{workers.missing && <small>작업자 조회 API 대기</small>}
      </figcaption>
    </figure>
  );
  return map;
}
