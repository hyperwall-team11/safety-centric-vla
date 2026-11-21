"use strict";

const state = {
  robots: [],
  tasks: [],
  logs: [],
  selectedTaskId: null,
  proposals: [],
  busy: false,
};

const statusNames = {
  CREATED: "생성",
  PLANNING: "계획 중",
  RUNNING: "실행 중",
  HELD: "보류",
  COMPLETED: "완료",
  FAILED: "실패",
  PROPOSED: "제안됨",
  APPROVED: "승인",
  REJECTED: "거절",
  EXECUTING: "실행 중",
  SUCCEEDED: "성공",
  IDLE: "대기",
  MOVING: "이동 중",
  ESTOP: "긴급 정지",
  ERROR: "오류",
};

const decisionNames = {
  APPROVE: "안전 승인",
  REJECT: "안전 거절",
  ESTOP: "긴급 정지",
  EXECUTE: "실행 요청",
  COMPLETE: "실행 완료",
  FAIL: "실행 실패",
};

const elements = {
  connection: document.querySelector("#connection-status"),
  refresh: document.querySelector("#refresh-button"),
  commandForm: document.querySelector("#command-form"),
  commandError: document.querySelector("#command-error"),
  preview: document.querySelector("#preview-result"),
  targetRobot: document.querySelector("#target-robot"),
  robotCount: document.querySelector("#robot-count"),
  activeTaskCount: document.querySelector("#active-task-count"),
  logCount: document.querySelector("#log-count"),
  robotList: document.querySelector("#robot-list"),
  robotForm: document.querySelector("#robot-form"),
  robotError: document.querySelector("#robot-error"),
  taskSearch: document.querySelector("#task-search"),
  statusFilter: document.querySelector("#status-filter"),
  taskList: document.querySelector("#task-list"),
  taskDetail: document.querySelector("#task-detail"),
  auditList: document.querySelector("#audit-list"),
  toast: document.querySelector("#toast"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function errorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).filter(Boolean).join(", ");
  if (detail && typeof detail === "object") return detail.message || detail.code || fallback;
  return fallback;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(errorMessage(payload, `요청에 실패했습니다. (${response.status})`));
  }
  return payload;
}

function setConnection(online) {
  elements.connection.classList.toggle("is-online", online);
  elements.connection.classList.toggle("is-offline", !online);
  elements.connection.lastChild.textContent = online ? " Backend 연결됨" : " Backend 연결 실패";
}

let toastTimer;
function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.classList.add("is-visible");
  toastTimer = setTimeout(() => elements.toast.classList.remove("is-visible"), 3200);
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = busy;
  });
}

function badge(status) {
  const safeStatus = escapeHtml(status);
  return `<span class="status-badge status-${safeStatus}">${escapeHtml(statusNames[status] || status)}</span>`;
}

function destinationText(destination) {
  if (!destination) return "목적지 없음";
  if (destination.type === "ZONE") return `Zone ${destination.zone_id}`;
  const position = destination.position || {};
  return `${destination.frame_id} (${position.x}, ${position.y}, ${position.z})`;
}

function formatDate(value) {
  if (!value) return "기록 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function renderMetrics() {
  const activeStatuses = new Set(["PLANNING", "RUNNING", "HELD"]);
  elements.robotCount.textContent = String(state.robots.length);
  elements.activeTaskCount.textContent = String(
    state.tasks.filter((task) => activeStatuses.has(task.status)).length,
  );
  elements.logCount.textContent = String(state.logs.length);
}

function renderRobotOptions() {
  const selected = elements.targetRobot.value;
  const options = state.robots.map((robot) => (
    `<option value="${escapeHtml(robot.robot_id)}">${escapeHtml(robot.robot_id)} (${escapeHtml(statusNames[robot.status] || robot.status)})</option>`
  ));
  elements.targetRobot.innerHTML = '<option value="">명령에서 자동 인식</option>' + options.join("");
  if (state.robots.some((robot) => robot.robot_id === selected)) {
    elements.targetRobot.value = selected;
  }
}

function renderRobots() {
  elements.robotList.classList.remove("loading-block");
  if (!state.robots.length) {
    elements.robotList.innerHTML = `
      <div class="empty-state">
        <strong>등록된 로봇이 없습니다.</strong>
        <p>아래 빠른 등록을 열고 시연용 로봇을 먼저 추가하세요.</p>
      </div>`;
    return;
  }
  elements.robotList.innerHTML = state.robots.map((robot) => `
    <div class="robot-item">
      <div>
        <p class="robot-id">${escapeHtml(robot.robot_id)}</p>
        <div class="robot-meta">
          <span>${escapeHtml(robot.robot_type)}</span>
          <span>최대 ${escapeHtml(robot.motion_limits.max_speed)} m/s</span>
        </div>
      </div>
      ${badge(robot.status)}
    </div>`).join("");
}

function filteredTasks() {
  const query = elements.taskSearch.value.trim().toLocaleLowerCase("ko-KR");
  const status = elements.statusFilter.value;
  return state.tasks.filter((task) => {
    const matchesQuery = !query || [task.instruction, task.target_robot_id]
      .some((value) => value.toLocaleLowerCase("ko-KR").includes(query));
    return matchesQuery && (!status || task.status === status);
  });
}

function renderTasks() {
  const tasks = filteredTasks();
  elements.taskList.classList.remove("loading-block");
  if (!tasks.length) {
    elements.taskList.innerHTML = `
      <div class="empty-state">
        <strong>표시할 작업이 없습니다.</strong>
        <p>새 명령으로 TaskOrder를 만들거나 필터 조건을 바꿔보세요.</p>
      </div>`;
    return;
  }
  elements.taskList.innerHTML = [...tasks].reverse().map((task) => `
    <button class="task-row ${task.task_id === state.selectedTaskId ? "is-selected" : ""}" type="button" data-task-id="${escapeHtml(task.task_id)}">
      <span>
        <span class="task-instruction">${escapeHtml(task.instruction)}</span>
        <span class="task-sub">${escapeHtml(task.target_robot_id)} / ${formatDate(task.created_at)}</span>
      </span>
      <span class="task-destination">${escapeHtml(destinationText(task.destination))}</span>
      ${badge(task.status)}
    </button>`).join("");
}

function proposalButtons(proposal) {
  if (proposal.status === "PROPOSED") {
    return `<button class="button button-primary button-small" type="button" data-evaluate="${escapeHtml(proposal.proposal_id)}">안전 평가</button>`;
  }
  if (proposal.status === "APPROVED") {
    return `<button class="button button-danger button-small" type="button" data-execute="${escapeHtml(proposal.proposal_id)}">실행 요청</button>`;
  }
  return "";
}

function renderDetail() {
  const task = state.tasks.find((item) => item.task_id === state.selectedTaskId);
  if (!task) {
    elements.taskDetail.innerHTML = `
      <div class="detail-empty">
        <p class="section-label">작업 상세</p>
        <h2>확인할 작업을 선택하세요.</h2>
        <p>TaskOrder와 ActionProposal 상태, 안전 평가 및 실행 버튼이 여기에 표시됩니다.</p>
      </div>`;
    return;
  }

  const activeProposal = state.proposals.some((proposal) =>
    ["PROPOSED", "APPROVED", "EXECUTING", "HELD"].includes(proposal.status),
  );
  const canCreateProposal = ["CREATED", "PLANNING"].includes(task.status) && !activeProposal;
  const proposalItems = state.proposals.length
    ? state.proposals.map((proposal) => `
        <div class="proposal-item">
          <div class="proposal-top">
            <strong>${escapeHtml(proposal.action_type)}</strong>
            ${badge(proposal.status)}
          </div>
          <p class="proposal-code">${escapeHtml(proposal.proposal_id)}</p>
          <p class="proposal-code">속도 상한 ${escapeHtml(proposal.speed_limit)} m/s / ${escapeHtml(proposal.model_name)}</p>
          <div class="proposal-actions">${proposalButtons(proposal)}</div>
        </div>`).join("")
    : '<div class="empty-state"><strong>ActionProposal이 없습니다.</strong><p>제안을 만든 뒤 안전 평가를 진행하세요.</p></div>';

  const manipulationField = task.target_object_id
    ? `<div class="field">
         <label for="proposal-manipulation">조작 방식</label>
         <select id="proposal-manipulation">
           <option value="">선택</option>
           <option value="PICK">PICK</option>
           <option value="PLACE">PLACE</option>
         </select>
       </div>`
    : "";

  const createForm = canCreateProposal
    ? `<div class="proposal-create">
         <div class="field-grid">
           <div class="field">
             <label for="proposal-speed">제안 속도 m/s</label>
             <input id="proposal-speed" type="number" min="0.01" step="0.01" placeholder="기본 상한 자동 적용" />
           </div>
           ${manipulationField}
         </div>
         <button class="button button-primary button-full" id="create-proposal-button" type="button">ActionProposal 만들기</button>
         <p class="form-error" id="proposal-error" aria-live="polite"></p>
       </div>`
    : "";

  elements.taskDetail.innerHTML = `
    <div class="detail-header">
      <div class="detail-title-row">
        <div>
          <p class="section-label">선택한 TaskOrder</p>
          <h2>${escapeHtml(task.target_robot_id)}</h2>
        </div>
        ${badge(task.status)}
      </div>
      <p class="detail-instruction">${escapeHtml(task.instruction)}</p>
      <div class="detail-meta">
        <div class="meta-item"><span>목적지</span><strong>${escapeHtml(destinationText(task.destination))}</strong></div>
        <div class="meta-item"><span>대상 물체</span><strong>${escapeHtml(task.target_object_id || "없음")}</strong></div>
      </div>
    </div>
    <div class="proposal-section">
      <h3>ActionProposal</h3>
      ${createForm}
      ${proposalItems}
    </div>`;
}

function renderAudit() {
  elements.auditList.classList.remove("loading-block");
  const logs = [...state.logs].reverse().slice(0, 8);
  if (!logs.length) {
    elements.auditList.innerHTML = `
      <div class="empty-state">
        <strong>아직 실행 기록이 없습니다.</strong>
        <p>ActionProposal을 평가하면 판단 근거가 여기에 쌓입니다.</p>
      </div>`;
    return;
  }
  elements.auditList.innerHTML = logs.map((log) => `
    <article class="audit-item">
      <time class="audit-time" datetime="${escapeHtml(log.timestamp)}">${formatDate(log.timestamp)}</time>
      <div class="audit-content">
        <strong>${escapeHtml(decisionNames[log.decision] || log.decision)} / ${escapeHtml(log.robot_id)}</strong>
        <p>${escapeHtml(log.result_message)}</p>
      </div>
    </article>`).join("");
}

function renderAll() {
  renderMetrics();
  renderRobotOptions();
  renderRobots();
  renderTasks();
  renderDetail();
  renderAudit();
}

async function loadAll({ quiet = false } = {}) {
  if (!quiet) elements.refresh.disabled = true;
  try {
    const [health, robots, tasks, logs] = await Promise.all([
      api("/health"),
      api("/robots"),
      api("/task-orders?order=asc&limit=100"),
      api("/execution-logs"),
    ]);
    setConnection(health.status === "ok");
    state.robots = robots;
    state.tasks = tasks;
    state.logs = logs;
    if (state.selectedTaskId && !tasks.some((task) => task.task_id === state.selectedTaskId)) {
      state.selectedTaskId = null;
      state.proposals = [];
    }
    renderAll();
  } catch (error) {
    setConnection(false);
    if (!quiet) showToast(error.message, true);
  } finally {
    elements.refresh.disabled = false;
  }
}

function commandPayload() {
  const form = new FormData(elements.commandForm);
  const payload = {
    instruction: String(form.get("instruction") || "").trim(),
    frame_id: String(form.get("frame_id") || "").trim(),
  };
  const targetRobotId = String(form.get("target_robot_id") || "").trim();
  if (targetRobotId) payload.target_robot_id = targetRobotId;
  return payload;
}

function renderPreview(preview, created = false) {
  elements.preview.classList.remove("empty-preview");
  elements.preview.innerHTML = `
    <div class="preview-grid">
      <div><span>${created ? "저장 결과" : "예상 동작"}</span><strong>${escapeHtml(preview.action_type || statusNames[preview.status] || preview.status)}</strong></div>
      <div><span>대상 로봇</span><strong>${escapeHtml(preview.target_robot_id)}</strong></div>
      <div><span>대상 물체</span><strong>${escapeHtml(preview.target_object_id || "없음")}</strong></div>
      <div><span>목적지</span><strong>${escapeHtml(destinationText(preview.destination))}</strong></div>
    </div>`;
}

async function handleCommandSubmit(event) {
  event.preventDefault();
  elements.commandError.textContent = "";
  const action = event.submitter?.dataset.action || "preview";
  const payload = commandPayload();
  if (!payload.instruction) {
    elements.commandError.textContent = "작업 내용을 입력하세요.";
    return;
  }
  setBusy(true);
  try {
    if (action === "preview") {
      const preview = await api("/task-orders/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderPreview(preview);
      showToast("명령 해석이 완료되었습니다.");
    } else {
      const idempotencyKey = globalThis.crypto?.randomUUID?.() || `console-${Date.now()}`;
      const task = await api("/task-orders/from-natural-language", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(payload),
      });
      renderPreview(task, true);
      state.selectedTaskId = task.task_id;
      state.proposals = [];
      await loadAll({ quiet: true });
      showToast("TaskOrder를 만들었습니다.");
    }
  } catch (error) {
    elements.commandError.textContent = error.message;
    elements.preview.classList.add("empty-preview");
    elements.preview.innerHTML = "<p>명령을 해석하지 못했습니다. 입력 내용을 확인하세요.</p>";
  } finally {
    setBusy(false);
  }
}

async function selectTask(taskId) {
  state.selectedTaskId = taskId;
  renderTasks();
  elements.taskDetail.innerHTML = '<div class="empty-state"><strong>작업 정보를 불러오는 중입니다.</strong></div>';
  try {
    state.proposals = await api(`/task-orders/${encodeURIComponent(taskId)}/action-proposals`);
    renderDetail();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function createProposal() {
  const task = state.tasks.find((item) => item.task_id === state.selectedTaskId);
  if (!task) return;
  const errorElement = document.querySelector("#proposal-error");
  const speedValue = document.querySelector("#proposal-speed")?.value;
  const manipulation = document.querySelector("#proposal-manipulation")?.value;
  const payload = {};
  if (speedValue) payload.speed_limit = Number(speedValue);
  if (task.target_object_id) {
    if (!manipulation) {
      errorElement.textContent = "물체 작업은 PICK 또는 PLACE를 선택해야 합니다.";
      return;
    }
    payload.manipulation = manipulation;
  }
  setBusy(true);
  try {
    await api(`/task-orders/${encodeURIComponent(task.task_id)}/action-proposals`, {
      method: "POST",
      headers: { "Idempotency-Key": globalThis.crypto?.randomUUID?.() || `proposal-${Date.now()}` },
      body: JSON.stringify(payload),
    });
    await loadAll({ quiet: true });
    await selectTask(task.task_id);
    showToast("ActionProposal을 만들었습니다.");
  } catch (error) {
    errorElement.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

async function evaluateProposal(proposalId) {
  setBusy(true);
  try {
    const proposal = await api(`/action-proposals/${encodeURIComponent(proposalId)}/evaluate`, { method: "POST" });
    await loadAll({ quiet: true });
    await selectTask(proposal.task_id);
    showToast(proposal.status === "APPROVED" ? "안전 평가를 통과했습니다." : "안전 규칙에 따라 거절되었습니다.", proposal.status !== "APPROVED");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function executeProposal(proposalId) {
  const confirmed = window.confirm("승인된 ActionProposal을 제어 어댑터로 전달할까요?");
  if (!confirmed) return;
  setBusy(true);
  try {
    const proposal = await api(`/action-proposals/${encodeURIComponent(proposalId)}/execute`, { method: "POST" });
    await loadAll({ quiet: true });
    await selectTask(proposal.task_id);
    showToast("실행 요청을 전달했습니다.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function registerRobot(event) {
  event.preventDefault();
  elements.robotError.textContent = "";
  const robotId = document.querySelector("#robot-id").value.trim();
  const adapter = document.querySelector("#adapter-id").value.trim();
  const frameId = document.querySelector("#robot-frame").value.trim();
  const maxSpeed = Number(document.querySelector("#robot-speed").value);
  const payload = {
    robot_id: robotId,
    robot_type: "MOBILE",
    control_adapter: adapter,
    status: "IDLE",
    pose: {
      frame_id: frameId,
      position: { x: 0, y: 0, z: 0 },
      orientation: { x: 0, y: 0, z: 0, w: 1 },
    },
    velocity: { x: 0, y: 0, z: 0 },
    motion_limits: { max_speed: maxSpeed, max_acceleration: 0.5 },
  };
  setBusy(true);
  try {
    await api("/robots", { method: "POST", body: JSON.stringify(payload) });
    await loadAll({ quiet: true });
    showToast(`${robotId} 로봇을 등록했습니다.`);
  } catch (error) {
    elements.robotError.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

elements.commandForm.addEventListener("submit", handleCommandSubmit);
elements.robotForm.addEventListener("submit", registerRobot);
elements.refresh.addEventListener("click", () => loadAll());
elements.taskSearch.addEventListener("input", renderTasks);
elements.statusFilter.addEventListener("change", renderTasks);

elements.taskList.addEventListener("click", (event) => {
  const row = event.target.closest("[data-task-id]");
  if (row) selectTask(row.dataset.taskId);
});

elements.taskDetail.addEventListener("click", (event) => {
  const createButton = event.target.closest("#create-proposal-button");
  const evaluateButton = event.target.closest("[data-evaluate]");
  const executeButton = event.target.closest("[data-execute]");
  if (createButton) createProposal();
  if (evaluateButton) evaluateProposal(evaluateButton.dataset.evaluate);
  if (executeButton) executeProposal(executeButton.dataset.execute);
});

loadAll();
