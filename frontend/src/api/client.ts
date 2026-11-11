import type {
  ActionProposal, ExecutionLog, HazardEvent, Robot, TaskOrder, TaskOrderPreview, WorkerSnapshot, Zone,
} from "./types";

// 개발 서버는 /api를 Backend로 프록시한다(vite.config.ts).
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

/** Backend에 아직 없는 API. 화면은 이 오류를 받으면 "API 대기" 상태를 보여 준다. */
export class MissingApiError extends Error {
  constructor(public endpoint: string) {
    super(`Backend API 미구현: ${endpoint}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    // 405: 조회 API가 없는 Backend(#75 이전). /workers/snapshot의 404는 "아직 관측 없음"이다.
    if (response.status === 405 && (path === "/hazards" || path === "/workers/snapshot")) {
      throw new MissingApiError(`GET ${path}`);
    }
    throw new ApiError(response.status, body?.detail ?? body);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body), headers });

export const api = {
  health: () => request<{ status: string }>("/health"),
  robots: () => request<Robot[]>("/robots"),
  zones: () => request<Zone[]>("/zones"),
  tasks: (status?: string) => request<TaskOrder[]>(`/task-orders?order=desc${status ? `&status=${status}` : ""}`),
  task: (taskId: string) => request<TaskOrder>(`/task-orders/${taskId}`),
  taskProposals: (taskId: string) => request<ActionProposal[]>(`/task-orders/${taskId}/action-proposals`),
  executionLogs: () => request<ExecutionLog[]>("/execution-logs"),

  // #75로 추가되는 조회 API. 없는 Backend에서는 "API 대기"로 표시한다.
  hazards: () => request<HazardEvent[]>("/hazards"),
  workers: () => request<WorkerSnapshot>("/workers/snapshot").catch((e) => {
    if (e instanceof ApiError && e.status === 404) return { observed_at: "", workers: [] } as WorkerSnapshot;
    throw e;
  }),

  previewTask: (instruction: string) => post<TaskOrderPreview>("/task-orders/preview", { instruction }),
  createTask: (instruction: string) =>
    post<TaskOrder>("/task-orders/from-natural-language", { instruction }, { "Idempotency-Key": crypto.randomUUID() }),
  createProposal: (taskId: string, speedLimit?: number) =>
    post<ActionProposal>(`/task-orders/${taskId}/action-proposals`, speedLimit ? { speed_limit: speedLimit } : {}),
  evaluate: (proposalId: string) => post<ActionProposal>(`/action-proposals/${proposalId}/evaluate`),
  execute: (proposalId: string) => post<ActionProposal>(`/action-proposals/${proposalId}/execute`),
  resolveHazard: (hazardId: string) => post<HazardEvent>(`/hazards/${hazardId}/resolve`, { resolved_by: "OPERATOR" }),
};
