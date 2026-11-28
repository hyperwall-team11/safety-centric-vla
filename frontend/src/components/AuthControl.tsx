import { useEffect, useState } from "react";
import { ApiError, api, getApiToken, setApiToken } from "../api/client";
import type { AuthStatus } from "../api/types";

export function AuthControl() {
  const [status, setStatus] = useState<AuthStatus>();
  const [editing, setEditing] = useState(false);
  const [token, setToken] = useState(getApiToken());

  useEffect(() => {
    api.authStatus()
      .then(setStatus)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) {
          setStatus({ enabled: true, subject: null, role: null });
        }
      });
  }, []);

  const save = () => {
    setApiToken(token);
    window.location.reload();
  };
  const logout = () => {
    setApiToken("");
    window.location.reload();
  };

  if (!status) return <span className="auth-state">권한 확인 중</span>;
  if (!status.enabled) return <span className="auth-state dev">개발 모드</span>;
  if (status.subject && status.role) {
    return (
      <div className="auth-state authenticated">
        <span>{status.subject} · {status.role}</span>
        <button onClick={logout}>로그아웃</button>
      </div>
    );
  }
  return (
    <div className="auth-control">
      {editing ? (
        <>
          <input type="password" value={token} onChange={(event) => setToken(event.target.value)}
                 placeholder="API token" aria-label="API token" />
          <button onClick={save} disabled={!token.trim()}>연결</button>
        </>
      ) : (
        <button className="auth-required" onClick={() => setEditing(true)}>관리자 인증 필요</button>
      )}
    </div>
  );
}
