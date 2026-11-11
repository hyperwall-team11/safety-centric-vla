import type { ReactNode } from "react";
import type { Polled } from "../hooks/usePolling";

export function Panel({ title, action, children, className = "" }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`.trim()}>
      <header className="panel-head">
        <h2>{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

/** 조회 결과의 로딩·오류·API 미구현 상태를 한곳에서 처리한다. */
export function Async<T>({ state, children }: { state: Polled<T>; children: (data: T) => ReactNode }) {
  if (state.missing) return <p className="empty-state">Backend API 대기 중 · {state.error?.message.replace("Backend API 미구현: ", "")}</p>;
  if (state.error && state.data === undefined) return <p className="empty-state error-state">불러오지 못했어요 · {state.error.message}</p>;
  if (state.data === undefined) return <p className="empty-state loading-state">데이터를 불러오는 중…</p>;
  return <>{children(state.data)}</>;
}

export function Stat({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      {detail && <span className="stat-detail">{detail}</span>}
    </div>
  );
}

/** 상태값은 스키마 그대로 표시한다. 색은 나중에 디자인에서 정한다(data-status로 연결). */
export function Status({ value }: { value: string | null | undefined }) {
  return <span className="status" data-status={value ?? ""}>{value ?? "-"}</span>;
}

export type Column<T> = { key: string; label: string; render: (row: T) => ReactNode };

export function Table<T>({ rows, columns, rowKey, empty = "없음" }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; empty?: string;
}) {
  if (rows.length === 0) return <p className="empty-state">{empty}</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead><tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row) => <tr key={rowKey(row)}>{columns.map((c) => <td key={c.key}>{c.render(row)}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  );
}

export function Placeholder({ label, height = 120 }: { label: string; height?: number }) {
  return <div className="placeholder" style={{ height }}><span className="camera-reticle" /><span>{label}</span></div>;
}

export const time = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour12: false }) : "-";

export function countBy<T>(rows: T[], key: (row: T) => string) {
  return rows.reduce<Record<string, number>>((acc, row) => ({ ...acc, [key(row)]: (acc[key(row)] ?? 0) + 1 }), {});
}

export const summary = (counts: Record<string, number>) =>
  Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(" · ") || "-";
