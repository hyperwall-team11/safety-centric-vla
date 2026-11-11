import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { useStateStream } from "../hooks/useStateStream";
import { CriticalBanner } from "./CriticalBanner";

const MENU = [
  { to: "/", label: "대시보드", end: true },
  { to: "/tasks/new", label: "작업 요청" },
  { to: "/proposals", label: "행동 제안" },
  { to: "/hazards", label: "위험" },
  { to: "/site", label: "현장" },
  { to: "/estop", label: "비상정지" },
  { to: "/logs", label: "판단 이력" },
];

export function Layout() {
  const health = usePolling(api.health, 5000, false);
  const stream = useStateStream();
  // WebSocket이 끊기거나 다시 붙으면 Backend 상태를 바로 확인한다(5초 주기를 기다리지 않음).
  const refreshHealth = health.refresh;
  useEffect(() => { refreshHealth(); }, [stream.connected, refreshHealth]);
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><i /><i /></span>
          <strong>VLA SAFETY</strong>
        </div>
        <nav className="topnav" aria-label="주요 메뉴">
          {MENU.map((m) => <NavLink key={m.to} to={m.to} end={m.end}>{m.label}</NavLink>)}
        </nav>
        <div className="system-state" data-state={health.error ? "down" : health.data ? "up" : "pending"}>
          <span className="system-dot" />
          <span>{health.error ? "연결 안 됨" : health.data ? "현장 정상 운영" : "확인 중"}</span>
          {health.data && !health.error && <span className="sync-mode">{stream.connected ? "실시간" : "주기 조회"}</span>}
        </div>
      </header>
      <main className="content">
        <CriticalBanner />
        <Outlet />
      </main>
    </div>
  );
}
