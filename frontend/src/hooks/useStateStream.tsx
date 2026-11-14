import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// WebSocket /ws/state(#76) 구독. Backend는 연결 직후·상태 변경 시·15초마다 전체 스냅샷을 보낸다.
// 화면은 스냅샷 내용 대신 revision만 보고, 바뀌면 각자 쓰던 조회 API를 다시 부른다.
// 연결이 없으면 usePolling이 기존 주기 조회로 동작한다.

export type StreamState = { connected: boolean; revision: number };

const StateStreamContext = createContext<StreamState>({ connected: false, revision: 0 });

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;
// heartbeat 15초 + 여유. 그동안 아무것도 오지 않으면 끊긴 것으로 본다.
const STALE_MS = 20000;

function streamUrl(): string {
  const base = import.meta.env.VITE_API_BASE ?? "/api";
  const url = new URL(`${base}/ws/state`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function StateStreamProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StreamState>({ connected: false, revision: 0 });

  useEffect(() => {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let staleTimer: number | undefined;
    let delay = RECONNECT_MIN_MS;
    let closed = false;
    // 재연결 시 서버 revision이 같아도 그 사이 놓친 변경이 있을 수 있으므로 수신마다 로컬 카운터를 올린다.
    let localRevision = 0;
    let lastServerRevision: number | undefined;

    const markStale = () => {
      window.clearTimeout(staleTimer);
      staleTimer = window.setTimeout(() => socket?.close(), STALE_MS);
    };

    const connect = () => {
      socket = new WebSocket(streamUrl());
      socket.onopen = () => { delay = RECONNECT_MIN_MS; markStale(); };
      socket.onmessage = (event) => {
        markStale();
        let message: { type?: string; revision?: number };
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type !== "STATE_SNAPSHOT") return;
        const first = lastServerRevision === undefined;
        // heartbeat(같은 revision)는 다시 조회하지 않는다. 첫 스냅샷은 연결 중 놓친 변경을 메우려고 반영한다.
        if (first || message.revision !== lastServerRevision) localRevision += 1;
        lastServerRevision = message.revision;
        setState({ connected: true, revision: localRevision });
      };
      socket.onclose = () => {
        window.clearTimeout(staleTimer);
        lastServerRevision = undefined;
        setState((s) => (s.connected ? { ...s, connected: false } : s));
        if (closed) return;
        retryTimer = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, RECONNECT_MAX_MS);
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      window.clearTimeout(staleTimer);
      socket?.close();
    };
  }, []);

  return <StateStreamContext.Provider value={state}>{children}</StateStreamContext.Provider>;
}

export const useStateStream = () => useContext(StateStreamContext);
