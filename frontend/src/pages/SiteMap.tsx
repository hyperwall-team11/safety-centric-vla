import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import type { Zone } from "../api/types";
import type { Point } from "../api/explain";
import { fetchSimScene } from "../api/sim";
import { useState } from "react";
import { sameRing } from "./siteMapGeometry";

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

// 시뮬레이션 동적 객체(로봇·작업자)가 이 시간(초) 넘게 갱신되지 않으면 오래된 관측으로 표시한다.
const SIM_STALE_S = 2;

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
  // 마지막 조회가 실패했으면(cctv_stream 종료·네트워크 단절) 이전 위치를 정답처럼 보이지 않도록 동적 객체를 숨긴다.
  const simLost = sim.error !== undefined;
  // 서버는 살아 있고 ROS 토픽만 끊긴 경우: 같은 서버 시계(observed_at) 기준으로 객체별 갱신 시각을 비교한다.
  const simAge = (updatedAt: number) => sim.data!.observed_at - updatedAt;
  const simStale = (updatedAt: number) => simAge(updatedAt) > SIM_STALE_S;
  const simDynamicOn = simOn && !simLost;
  // 시뮬레이션 장면은 world 좌표계다. 다른 좌표계(map 등)의 등록 항목은 변환 없이 같은 위치로 비교하지 않는다.
  const simFrame = sim.data?.frame_id;
  const sameFrame = (frameId: string) => simFrame === undefined || frameId === simFrame;
  const otherFrames = simOn ? [...new Set([
    ...(zones.data ?? []).map((z) => z.frame_id),
    ...(robots.data ?? []).map((r) => r.pose.frame_id),
    ...(workers.data?.workers ?? []).map((w) => w.pose.frame_id),
  ].filter((f) => !sameFrame(f)))] : [];
  const flip = (y: number) => -y;

  // 계획 경로: trajectory가 있으면 그대로, 없으면 로봇 위치 → 목적지 구역 중심을 점선으로 잇는다.
  let planPts: Point[] = [];
  let planEstimated = false;
  if (plan) {
    planPts = plan.path;
    if (planPts.length < 2) {
      const robot = robots.data?.find((r) => r.robot_id === plan.robotId);
      const zone = zones.data?.find((z) => z.zone_id === plan.toZoneId);
      if (robot && zone && ring(zone).length > 0) {
        planPts = [{ x: robot.pose.position.x, y: robot.pose.position.y }, centroid(ring(zone))];
        planEstimated = true;
      }
    }
  }
  const planLine = planPts.length >= 2
    ? { points: planPts.map((p) => `${p.x},${flip(p.y)}`).join(" "), estimated: planEstimated, end: planPts[planPts.length - 1] }
    : undefined;

  // CCTV 시야 삼각형. 작업 영역(구역·작업대) 중심을 조금 지나는 길이까지만 그린다.
  const fovs = simOn ? (() => {
    const pts = [...sim.data!.zones, ...sim.data!.tables].flatMap((z) => z.points);
    const cx = pts.reduce((a, p) => a + p[0], 0) / Math.max(pts.length, 1);
    const cy = pts.reduce((a, p) => a + p[1], 0) / Math.max(pts.length, 1);
    return sim.data!.cameras.map((c) => {
      const [x, y] = c.position;
      const L = Math.hypot(cx - x, cy - y) * 1.25;
      const a1 = c.yaw - c.hfov / 2, a2 = c.yaw + c.hfov / 2;
      return { camera: c, tri: [[x, y], [x + L * Math.cos(a1), y + L * Math.sin(a1)], [x + L * Math.cos(a2), y + L * Math.sin(a2)]] as [number, number][] };
    });
  })() : [];

  // 지도 범위: 등록된 구역·로봇·작업자, 시뮬레이션 장면(CCTV 시야 포함), 계획 경로를 모두 담도록 맞춘다(여백 0.8m).
  const xs: number[] = [], ys: number[] = [];
  const add = (x: number, y: number) => { xs.push(x); ys.push(y); };
  for (const z of zones.data ?? []) for (const [x, y] of ring(z)) add(x, y);
  for (const r of robots.data ?? []) add(r.pose.position.x, r.pose.position.y);
  for (const w of workers.data?.workers ?? []) add(w.pose.position.x, w.pose.position.y);
  if (simOn) {
    const b = sim.data!.bounds;
    if (b) { add(b[0], b[1]); add(b[2], b[3]); }
    for (const f of fovs) for (const [x, y] of f.tri) add(x, y);
    for (const p of sim.data!.props ?? []) for (const [x, y] of p.points) add(x, y);
  }
  if (simDynamicOn) {
    for (const w of sim.data!.workers) add(...w.position);
    for (const r of sim.data!.robots) add(...r.position);
  }
  for (const p of planPts) add(p.x, p.y);
  const isEmpty = xs.length === 0;
  const pad = 0.8;
  const view = isEmpty
    ? { x: -4, y: -4, w: 8, h: 8 }
    : { x: Math.min(...xs) - pad, y: -Math.max(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + 2 * pad, h: Math.max(...ys) - Math.min(...ys) + 2 * pad };
  // 글자·아이콘 크기를 지도 크기에 맞춘다(기준 8m)
  const k = Math.max(view.w, view.h) / 8;

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
            {fovs.map(({ camera: c, tri }) => (
              <g key={c.sensor_id} className="sim-camera">
                <polygon className="fov" points={tri.map(([x, y]) => `${x},${flip(y)}`).join(" ")} />
                <circle cx={c.position[0]} cy={flip(c.position[1])} r={0.12 * k} />
                <text x={c.position[0] + 0.18 * k} y={flip(c.position[1]) + 0.06 * k} style={{ fontSize: 0.19 * k }}>{c.sensor_id}</text>
              </g>
            ))}
            {sim.data!.zones.map((z) => {
              // 등록된 구역과 겹쳐 그려 배치를 비교한다. 같으면 라벨을 생략하고, 다르면 불일치로 표시한다.
              const registered = zones.data?.find((b) => b.zone_id === z.zone_id);
              const frameDiff = registered !== undefined && !sameFrame(registered.frame_id);
              const mismatch = registered !== undefined && !frameDiff && !sameRing(ring(registered), z.points);
              const label = !registered ? `${z.zone_id} (미등록)`
                // 좌표계가 다르면 외곽선만 흐리게 그린다. 안내는 범례(otherFrames) 한 줄로 충분하다
                : frameDiff ? undefined
                : mismatch ? `${z.zone_id} 시뮬레이션 배치 불일치` : undefined;
              return (
                <g key={z.zone_id} className="sim-zone" data-type={z.zone_type} data-registered={registered ? true : undefined} data-mismatch={mismatch || undefined} data-frame-diff={frameDiff || undefined}>
                  <polygon points={z.points.map(([x, y]) => `${x},${flip(y)}`).join(" ")} />
                  {label && <text x={z.points[0][0]} y={flip(z.points[0][1]) + (registered ? 0.28 : -0.1) * k} style={{ fontSize: 0.19 * k }}>{label}</text>}
                </g>
              );
            })}
            {(sim.data!.props ?? []).map((p) => (
              <g key={p.name} className="sim-prop" data-kind={p.kind}>
                <polygon points={p.points.map(([x, y]) => `${x},${flip(y)}`).join(" ")}><title>{p.label}</title></polygon>
                {!["fence", "rail", "pole", "manhole"].includes(p.kind) && (() => {
                  const c = centroid(p.points);
                  return <text x={c.x} y={flip(c.y) + 0.06 * k} style={{ fontSize: 0.17 * k }}>{p.label}</text>;
                })()}
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
        {simDynamicOn && sim.data!.robots.map((r) => (
          <g key={`sim-${r.robot_id}`} className="sim-robot" data-stale={simStale(r.updated_at) || undefined}>
            <circle cx={r.position[0]} cy={flip(r.position[1])} r={0.3} />
            <line x1={r.position[0]} y1={flip(r.position[1])} x2={r.position[0] + 0.45 * Math.cos(r.yaw)} y2={flip(r.position[1] + 0.45 * Math.sin(r.yaw))} />
            <title>{simStale(r.updated_at) ? `${r.robot_id} 마지막 관측 (${simAge(r.updated_at).toFixed(0)}초 전)` : `${r.robot_id} 시뮬레이션 실제 위치`}</title>
          </g>
        ))}
        {simDynamicOn && sim.data!.workers.map((w) => (
          <g key={`sim-${w.worker_id}`} className="sim-worker" data-stale={simStale(w.updated_at) || undefined}>
            <circle cx={w.position[0]} cy={flip(w.position[1])} r={0.22} />
            <text x={w.position[0] + 0.3} y={flip(w.position[1]) + 0.06 * k} style={{ fontSize: 0.17 * k }}>{w.worker_id}{simStale(w.updated_at) && ` (마지막 관측 ${simAge(w.updated_at).toFixed(0)}초 전)`}</text>
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
            {simLost && <small className="sim-lost">연결 끊김 · 위치 숨김</small>}
            {otherFrames.length > 0 && <small className="sim-frame-diff">좌표계 불일치: {otherFrames.join(", ")} 항목은 시뮬레이션({simFrame})과 비교하지 않음</small>}
          </label>
        )}
      </figcaption>
    </figure>
  );
  return map;
}
