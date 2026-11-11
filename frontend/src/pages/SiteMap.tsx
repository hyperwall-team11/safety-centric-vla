import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Panel, Placeholder } from "../components/ui";
import type { Zone } from "../api/types";

function ring(zone: Zone): [number, number][] {
  const g = zone.geometry as { points?: number[][]; coordinates?: number[][][] };
  const pts = g.points ?? g.coordinates?.[0] ?? [];
  return pts.map((p) => [p[0], p[1]]);
}

/** 바닥 평면도(x 오른쪽, y 위). 작업자 위치는 GET /workers/snapshot이 생기면 표시된다. */
export function SiteMap({ compact = false }: { compact?: boolean }) {
  const zones = usePolling(api.zones, 5000);
  const robots = usePolling(api.robots);
  const workers = usePolling(api.workers);
  const isEmpty = (zones.data?.length ?? 0) === 0 && (robots.data?.length ?? 0) === 0;
  const view = { x: -4, y: -4, w: 8, h: 8 }; // 시나리오 장면 기준 범위(m)
  const flip = (y: number) => -y;

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
          <g key={z.zone_id} className="zone" data-type={z.zone_type}>
            <polygon points={ring(z).map(([x, y]) => `${x},${flip(y)}`).join(" ")} />
            {ring(z)[0] && <text x={ring(z)[0][0]} y={flip(ring(z)[0][1]) - 0.1}>{z.zone_id}</text>}
          </g>
        ))}
        {(robots.data ?? []).map((r) => (
          <g key={r.robot_id} className="robot">
            <rect x={r.pose.position.x - 0.25} y={flip(r.pose.position.y) - 0.25} width={0.5} height={0.5} />
            <text x={r.pose.position.x + 0.3} y={flip(r.pose.position.y)}>{r.robot_id}</text>
          </g>
        ))}
        {(workers.data?.workers ?? []).map((w) => (
          <circle key={w.worker_id} className="worker" cx={w.pose.position.x} cy={flip(w.pose.position.y)} r={0.2} />
        ))}
      </svg>
      <figcaption className="map-legend">
        <span><i className="legend-robot" /> 로봇</span><span><i className="legend-worker" /> 작업자</span>{workers.missing && <small>작업자 조회 API 대기</small>}
      </figcaption>
    </figure>
  );
  if (compact) return map;

  return (
    <>
      <div className="page-head"><div className="page-head-copy"><span className="eyebrow">CCTV · 현장 지도</span><h1>현장을, 더<br />선명하게.</h1><p>공간 구역과 사람, 로봇 그리고 위험을 한 화면에서 확인합니다.</p></div></div>
      <div className="columns">
        <Panel title="현장 지도" className="map-panel">{map}</Panel>
        <Panel title="CCTV" className="camera-panel">
          <div className="stack">
            <Placeholder label="cctv_1 영상 (ROS 2 → 웹 브리지 필요, #55)" height={180} />
            <Placeholder label="cctv_2 영상" height={180} />
          </div>
        </Panel>
      </div>
    </>
  );
}
