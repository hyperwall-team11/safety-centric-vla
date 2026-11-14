import { useEffect, useState } from "react";

// control/ros2_ws/src/mujoco_bridge/mujoco_bridge/cctv_stream.py가 내보내는 MJPEG.
// 개발 서버는 /cctv를 cctv_stream(기본 127.0.0.1:8081)으로 프록시한다(vite.config.ts).
const CCTV_BASE = import.meta.env.VITE_CCTV_BASE ?? "/cctv";
const RETRY_MS = 5000;
// 마지막 프레임이 이보다 오래되면 "신호 끊김"으로 본다(원본 15Hz 기준 넉넉히).
const STALE_SEC = 3;

type CameraInfo = { sensor_id: string; last_frame_at: number | null };
type State = "connecting" | "live" | "stale" | "offline";

/** 카메라 상태 목록. 스트림 노드가 없으면 undefined. */
function useCameraStatus() {
  const [cams, setCams] = useState<CameraInfo[]>();
  useEffect(() => {
    let alive = true;
    const load = () => fetch(`${CCTV_BASE}/cameras`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((v: CameraInfo[]) => alive && setCams(v))
      .catch(() => alive && setCams(undefined));
    load();
    const t = window.setInterval(load, 2000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  return cams;
}

function CameraTile({ sensorId, info, reachable }: { sensorId: string; info?: CameraInfo; reachable: boolean }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // 스트림이 끊기면 잠시 뒤 다시 연결한다.
  useEffect(() => {
    if (!failed) return;
    const t = window.setTimeout(() => { setFailed(false); setLoaded(false); setAttempt((a) => a + 1); }, RETRY_MS);
    return () => window.clearTimeout(t);
  }, [failed]);

  const age = info?.last_frame_at ? Date.now() / 1000 - info.last_frame_at : undefined;
  const state: State = !reachable || failed ? "offline"
    : age === undefined ? "connecting"
    : age > STALE_SEC ? "stale"
    : loaded ? "live" : "connecting";
  const label = { live: "LIVE", connecting: "연결 중", stale: "신호 끊김", offline: "영상 없음" }[state];

  return (
    <figure className="cctv-tile" data-state={state}>
      {reachable && !failed && (
        <img key={attempt} src={`${CCTV_BASE}/stream/${sensorId}?n=${attempt}`} alt={`${sensorId} 실시간 영상`}
             onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
      )}
      {state !== "live" && (
        <div className="cctv-empty">
          <span className="camera-reticle" />
          <span>{state === "offline" ? "CCTV 스트림에 연결할 수 없습니다" : state === "stale" ? "최근 영상이 들어오지 않습니다" : "영상을 기다리는 중"}</span>
          {state === "offline" && <small><code>pixi run bridge</code> · <code>pixi run cctv-stream</code></small>}
        </div>
      )}
      <figcaption><span className="cctv-dot" />{sensorId}<em>{label}</em></figcaption>
    </figure>
  );
}

export function CctvView({ sensorIds = ["cctv_1", "cctv_2"] }: { sensorIds?: string[] }) {
  const cams = useCameraStatus();
  return (
    <div className="cctv-grid">
      {sensorIds.map((id) => (
        <CameraTile key={id} sensorId={id} info={cams?.find((c) => c.sensor_id === id)} reachable={cams !== undefined} />
      ))}
    </div>
  );
}
