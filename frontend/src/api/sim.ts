// 시뮬레이션 장면(MuJoCo ① 정답). control의 cctv_stream 노드가 GET /scene으로 내보낸다
// (mujoco_bridge/sim_scene.py). 온톨로지 상태가 아니므로 지도에서는 "시뮬레이션" 레이어로만 그린다.
const CCTV_BASE = import.meta.env.VITE_CCTV_BASE ?? "/cctv";

type XY = [number, number];
export type SimScene = {
  source: string;
  frame_id: string;
  zones: { zone_id: string; zone_type: string; points: XY[] }[];
  tables: { name: string; points: XY[] }[];
  cameras: { sensor_id: string; position: [number, number, number]; yaw: number; hfov: number }[];
  robots: { robot_id: string; position: XY; yaw: number; updated_at: number }[];
  workers: { worker_id: string; position: XY; updated_at: number }[];
  bounds: [number, number, number, number] | null;
  observed_at: number;
};

export async function fetchSimScene(): Promise<SimScene> {
  const r = await fetch(`${CCTV_BASE}/scene`);
  if (!r.ok) throw new Error(`시뮬레이션 장면 없음 (HTTP ${r.status})`);
  return r.json();
}
