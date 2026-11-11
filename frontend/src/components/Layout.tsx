import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { CriticalBanner } from "./CriticalBanner";

const MENU = [
  { to: "/", label: "대시보드", end: true },
  { to: "/tasks/new", label: "작업 요청" },
  { to: "/proposals", label: "행동 제안" },
  { to: "/hazards", label: "위험 모니터링" },
  { to: "/site", label: "CCTV·현장 지도" },
  { to: "/estop", label: "비상정지·복귀" },
  { to: "/logs", label: "판단 이력" },
];

export function Layout() {
  const health = usePolling(api.health, 5000);
  return (
    <div className="app">
      <header className="topbar">
        <strong>VLA 안전 운영</strong>
        <span className="note">서버 {health.error ? "연결 안 됨" : health.data ? "연결됨" : "확인 중"}</span>
      </header>
      <nav className="sidebar" aria-label="메뉴">
        {MENU.map((m) => <NavLink key={m.to} to={m.to} end={m.end}>{m.label}</NavLink>)}
      </nav>
      <main className="content">
        <CriticalBanner />
        <Outlet />
      </main>
    </div>
  );
}
