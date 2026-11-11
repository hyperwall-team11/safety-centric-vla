import { useCallback, useEffect, useRef, useState } from "react";
import { MissingApiError } from "../api/client";
import { useStateStream } from "./useStateStream";

export type Polled<T> = {
  data: T | undefined;
  error: Error | undefined;
  missing: boolean; // Backend API 미구현
  loading: boolean;
  refresh: () => void;
};

// 상태 조회. /ws/state가 연결되어 있으면 revision이 바뀔 때만 다시 조회하고,
// 끊겨 있으면 intervalMs(기본 1초) 주기 조회로 동작한다. live=false면 항상 주기 조회.
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs = 1000, live = true): Polled<T> {
  const stream = useStateStream();
  const streamed = live && stream.connected;
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const timerRef = useRef<number>();

  const refresh = useCallback(() => {
    fetcherRef.current()
      .then((value) => { setData(value); setError(undefined); })
      .catch((err: Error) => {
        setError(err);
        // 없는 API는 반복 조회하지 않는다. 새로고침하면 다시 확인한다.
        if (err instanceof MissingApiError) window.clearInterval(timerRef.current);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (streamed) return;
    refresh();
    timerRef.current = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(timerRef.current);
  }, [refresh, intervalMs, streamed]);

  // 스트림 연결 중에는 상태가 바뀐 스냅샷이 올 때마다(연결 직후 포함) 한 번 조회한다.
  useEffect(() => {
    if (streamed) refresh();
  }, [refresh, streamed, stream.revision]);

  return { data, error, missing: error instanceof MissingApiError, loading, refresh };
}
