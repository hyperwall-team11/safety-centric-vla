import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import type { Zone } from "../api/types";
import type { Point } from "../api/explain";
import { fetchSimScene } from "../api/sim";
import { useState } from "react";

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
  // 시뮬레이션 정답(구역 표시·작업대·CCTV 시야·로봇·작업자). Backend가 아니라 cctv_stream에서 받으므로 실시간 구독과 무관하게 0.5초마다 조회
  const sim = usePolling(fetchSimScene, 500, false);
  const [showSim, setShowSim] = useState(true);
  const simOn = showSim && sim.data !== undefined;
  const flip = (y: number) => -y;

  // 지도 범위: 등록된 구역·로봇·작업자와 시뮬레이션 장면을 모두 담도록 맞춘다(여백 0.8m).
  const xs: number[] = [], ys: number[] = [];
  const add = (x: number, y: number) => { xs.push(x); ys.push(y); };
  for (const z of zones.data ?? []) for (const [x, y] of ring(z)) add(x, y);
  for (const r of robots.data ?? []) add(r.pose.position.x, r.pose.position.y);
  for (const w of workers.data?.workers ?? []) add(w.pose.position.x, w.pose.position.y);
  if (simOn) {
    const b = sim.data!.bounds;
    if (b) { add(b[0], b[1]); add(b[2], b[3]); }
    for (const w of sim.data!.workers) add(...w.position);
    for (const r of sim.data!.robots) add(...r.position);
  }
  const isEmpty = xs.length === 0;
  const pad = 0.8;
  const view = isEmpty
    ? { x: -4, y: -4, w: 8, h: 8 }
    : { x: Math.min(...xs) - pad, y: -Math.max(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + 2 * pad, h: Math.max(...ys) - Math.min(...ys) + 2 * pad };
  // 글자·아이콘 크기를 지도 크기에 맞춘다(기준 8m)
  const k = Math.max(view.w, view.h) / 8;

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
        {simOn && (
          <g className="sim-layer" aria-label="시뮬레이션 장면">
            {sim.data!.cameras.map((c) => {
              // 시야는 작업 영역(구역·작업대) 중심을 조금 지나는 길이까지만 그린다
              const pts = [...sim.data!.zones, ...sim.data!.tables].flatMap((z) => z.points);
              const cx = pts.reduce((a, p) => a + p[0], 0) / Math.max(pts.length, 1);
              const cy = pts.reduce((a, p) => a + p[1], 0) / Math.max(pts.length, 1);
              const L = Math.hypot(cx - c.position[0], cy - c.position[1]) * 1.25;
              const [x, y] = c.position;
              const a1 = c.yaw - c.hfov / 2, a2 = c.yaw + c.hfov / 2;
              return (
                <g key={c.sensor_id} className="sim-camera">
                  <polygon className="fov" points={`${x},${flip(y)} ${x + L * Math.cos(a1)},${flip(y + L * Math.sin(a1))} ${x + L * Math.cos(a2)},${flip(y + L * Math.sin(a2))}`} />
                  <circle cx={x} cy={flip(y)} r={0.12 * k} />
                  <text x={x + 0.18 * k} y={flip(y) + 0.06 * k} style={{ fontSize: 0.19 * k }}>{c.sensor_id}</text>
                </g>
              );
            })}
            {sim.data!.zones.filter((z) => !(zones.data ?? []).some((b) => b.zone_id === z.zone_id)).map((z) => (
              <g key={z.zone_id} className="sim-zone" data-type={z.zone_type}>
                <polygon points={z.points.map(([x, y]) => `${x},${flip(y)}`).join(" ")} />
                <text x={z.points[0][0]} y={flip(z.points[0][1]) - 0.1 * k} style={{ fontSize: 0.19 * k }}>{z.zone_id} (미등록)</text>
              </g>
            ))}
            {sim.data!.tables.map((t) => (
              <polygon key={t.name} className="sim-table" points={t.points.map(([x, y]) => `${x},${flip(y)}`).join(" ")}><title>{t.name}</title></polygon>
            ))}
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
            {ring(z)[0] && <text x={ring(z)[0][0]} y={flip(ring(z)[0][1]) - 0.1 * k} style={{ fontSize: 0.19 * k }}>{z.zone_id}</text>}
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
            <text x={r.pose.position.x + 0.32} y={flip(r.pose.position.y) + 0.06 * k} style={{ fontSize: 0.19 * k }}>{r.robot_id}</text>
          </g>
        ))}
        {simOn && sim.data!.robots.map((r) => (
          <g key={`sim-${r.robot_id}`} className="sim-robot">
            <circle cx={r.position[0]} cy={flip(r.position[1])} r={0.3} />
            <line x1={r.position[0]} y1={flip(r.position[1])} x2={r.position[0] + 0.45 * Math.cos(r.yaw)} y2={flip(r.position[1] + 0.45 * Math.sin(r.yaw))} />
            <title>{r.robot_id} 시뮬레이션 실제 위치</title>
          </g>
        ))}
        {simOn && sim.data!.workers.map((w) => (
          <g key={`sim-${w.worker_id}`} className="sim-worker">
            <circle cx={w.position[0]} cy={flip(w.position[1])} r={0.22} />
            <text x={w.position[0] + 0.3} y={flip(w.position[1]) + 0.06 * k} style={{ fontSize: 0.17 * k }}>{w.worker_id}</text>
          </g>
        ))}
        {(workers.data?.workers ?? []).map((w) => (
          <circle key={w.worker_id} className={`worker${violation?.workerIds.includes(w.worker_id) ? " violation" : ""}`} cx={w.pose.position.x} cy={flip(w.pose.position.y)} r={0.2} />
        ))}
      </svg>
      <figcaption className="map-legend">
        <span><i className="legend-robot" /> 로봇</span><span><i className="legend-worker" /> 작업자</span><span><i className="legend-hazard" /> 열린 위험</span>{planLine && <span><i className="legend-plan" /> {planLine.estimated ? "예상 이동(경로 없음)" : "계획 경로"}</span>}{workers.missing && <small>작업자 조회 API 대기</small>}
        {sim.data && (
          <label className="sim-toggle">
            <input type="checkbox" checked={showSim} onChange={(e) => setShowSim(e.target.checked)} />
            시뮬레이션 정답 ({sim.data.source})
          </label>
        )}
      </figcaption>
    </figure>
  );
  return map;
}
