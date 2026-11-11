import { useCallback, useEffect, useRef, useState } from "react";
import { MissingApiError } from "../api/client";

export type Polled<T> = {
  data: T | undefined;
  error: Error | undefined;
  missing: boolean; // Backend API 미구현
  loading: boolean;
  refresh: () => void;
};

// WebSocket /ws/state가 생기기 전까지 쓰는 주기 조회. 기본 1초.
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs = 1000): Polled<T> {
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
    refresh();
    timerRef.current = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(timerRef.current);
  }, [refresh, intervalMs]);

  return { data, error, missing: error instanceof MissingApiError, loading, refresh };
}
