import { Link } from "react-router-dom";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { Icon } from "./Icon";

/** 열린 CRITICAL 위험이 있으면 모든 화면 상단에 고정한다. */
export function CriticalBanner() {
  const hazards = usePolling(api.hazards);
  const open = (hazards.data ?? []).filter((h) => h.severity === "CRITICAL" && h.status !== "RESOLVED");
  if (open.length === 0) return null;
  const first = open[0];
  return (
    <div className="banner" role="alert">
      <Icon name="hazard" />
      <span><strong>CRITICAL 위험 {open.length}건</strong><small>{first.hazard_type} · {first.zone_id} · 관련 로봇 {first.related_robot_ids.join(", ") || "-"}</small></span>
      <Link to="/hazards">위험 보기 <Icon name="arrow" /></Link>
    </div>
  );
}
