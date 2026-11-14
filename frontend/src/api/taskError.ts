export type TaskErrorView = {
  title: string;
  message: string;
  hint?: string;
};

const HINTS: Record<string, { title: string; hint: string }> = {
  INVALID_INSTRUCTION: {
    title: "실행 명령인지 확인해 주세요",
    hint: "질문이나 조건문 대신 ‘이동해’, ‘옮겨줘’처럼 한 가지 행동을 직접 요청해 주세요.",
  },
  UNSUPPORTED_CONTROL_ACTION: {
    title: "정지·재개 명령은 여기서 만들 수 없습니다",
    hint: "비상정지·복귀 화면에서 안전 절차에 따라 처리해 주세요.",
  },
  ROBOT_NOT_FOUND: {
    title: "등록되지 않은 로봇입니다",
    hint: "현장 지도에 표시된 로봇 ID를 선택하거나 정확히 입력해 주세요.",
  },
  ROBOT_AMBIGUOUS: {
    title: "대상 로봇을 하나만 지정해 주세요",
    hint: "한 명령에는 로봇 ID를 하나만 포함해 주세요.",
  },
  ROBOT_CONFLICT: {
    title: "선택한 로봇과 명령 속 로봇이 다릅니다",
    hint: "요청에 지정한 로봇 ID와 명령 속 로봇 ID가 같은지 확인해 주세요.",
  },
  ROBOT_REQUIRED: {
    title: "대상 로봇이 필요합니다",
    hint: "지도에서 로봇을 누르거나 ‘robot-01 로봇을’처럼 ID를 포함해 주세요.",
  },
  DESTINATION_INVALID: {
    title: "목적지 형식을 확인해 주세요",
    hint: "구역명 또는 ‘좌표 (x, y, z)’ 형식으로 입력해 주세요.",
  },
  DESTINATION_REQUIRED: {
    title: "목적지가 필요합니다",
    hint: "지도에서 구역을 누르거나 이동할 구역·좌표를 명령에 포함해 주세요.",
  },
  DESTINATION_AMBIGUOUS: {
    title: "목적지를 하나만 지정해 주세요",
    hint: "한 명령에 구역이나 좌표를 하나만 포함해 주세요.",
  },
  OBJECT_AMBIGUOUS: {
    title: "대상 물체를 하나만 지정해 주세요",
    hint: "‘상자3’처럼 옮길 물체 ID를 하나만 포함해 주세요.",
  },
  ACTION_INVALID: {
    title: "행동을 해석할 수 없습니다",
    hint: "이동은 ‘이동해’, 물체 운반은 ‘옮겨줘’처럼 지원되는 동사를 사용해 주세요.",
  },
  ACTION_UNSUPPORTED: {
    title: "아직 지원하지 않는 행동입니다",
    hint: "현재 작업 요청에서는 이동과 물체 운반 명령을 사용할 수 있습니다.",
  },
};

type StructuredDetail = { code?: unknown; message?: unknown };
type ValidationDetail = { loc?: unknown; msg?: unknown };

export function explainTaskError(detail: unknown): TaskErrorView {
  if (typeof detail === "string") {
    return { title: "명령을 처리하지 못했습니다", message: detail };
  }

  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const structured = detail as StructuredDetail;
    if (typeof structured.code === "string" && typeof structured.message === "string") {
      const explanation = HINTS[structured.code];
      return {
        title: explanation?.title ?? "명령을 다시 확인해 주세요",
        message: structured.message,
        hint: explanation?.hint,
      };
    }
  }

  if (Array.isArray(detail)) {
    const first = detail[0] as ValidationDetail | undefined;
    const locationParts = Array.isArray(first?.loc) ? first.loc : [];
    const location = locationParts.length > 0 ? locationParts.join(" → ") : "입력값";
    const instructionError = locationParts.includes("instruction");
    return {
      title: "입력 형식이 올바르지 않습니다",
      message: typeof first?.msg === "string" ? `${location}: ${first.msg}` : "명령 입력값을 확인해 주세요.",
      hint: instructionError
        ? "명령은 비워 둘 수 없으며 1,000자 이하여야 합니다."
        : undefined,
    };
  }

  return { title: "명령을 처리하지 못했습니다", message: "잠시 후 다시 시도해 주세요." };
}
