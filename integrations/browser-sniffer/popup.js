import { ORCHESTRATOR_CONFIG, snifferPlanEndpoint } from "./orchestrator-config.js";

const endpointEl = document.querySelector("#endpoint");
const serviceEl = document.querySelector("#service");
const captureSessionEl = document.querySelector("#captureSession");
const newSessionEl = document.querySelector("#newSession");
const latestStatusEl = document.querySelector("#latestStatus");
const latestSummaryEl = document.querySelector("#latestSummary");
const operatorCommandEl = document.querySelector("#operatorCommand");
const copyCommandEl = document.querySelector("#copyCommand");
const handoffSummaryEl = document.querySelector("#handoffSummary");
const copyHandoffCommandEl = document.querySelector("#copyHandoffCommand");
const operatorStatusEl = document.querySelector("#operatorStatus");
const operatorSummaryEl = document.querySelector("#operatorSummary");
const retryableTasksEl = document.querySelector("#retryableTasks");
const copyRetryableCommandEl = document.querySelector("#copyRetryableCommand");
const copyResumeCommandEl = document.querySelector("#copyResumeCommand");
const lastSubmitEl = document.querySelector("#lastSubmit");
const recentSubmitsEl = document.querySelector("#recentSubmits");
const SESSION_KEY = "captureSessionId";

endpointEl.textContent = snifferPlanEndpoint();
let operatorCommand = "";
let handoffCommand = "";
let retryableCommand = "python -m video_orchestrator.cli task retryable --format json";
let resumeCommand = "python -m video_orchestrator.cli task resume <task-id> --dry-run --format json";

function valueOrEmpty(value) {
  if (value === null || value === undefined || value === "") {
    return "暂无";
  }
  return String(value);
}

function renderLines(items) {
  return items.map(([label, value]) => `${label}：${valueOrEmpty(value)}`).join("\n");
}

async function checkService() {
  try {
    const response = await fetch(`${ORCHESTRATOR_CONFIG.apiBaseUrl}/config`);
    const payload = await response.json();
    serviceEl.textContent = response.ok ? payload.check.status : `HTTP ${response.status}`;
    serviceEl.classList.toggle("ok", response.ok && payload.check.status === "ok");
    serviceEl.classList.toggle("bad", !response.ok || payload.check.status !== "ok");
  } catch (error) {
    serviceEl.textContent = "离线";
    serviceEl.classList.add("bad");
  }
}

async function renderLastSubmit() {
  const { lastSubmit, recentSubmits } = await chrome.storage.local.get(["lastSubmit", "recentSubmits"]);
  lastSubmitEl.textContent = lastSubmit ? JSON.stringify(lastSubmit, null, 2) : "暂无";
  recentSubmitsEl.textContent = Array.isArray(recentSubmits) && recentSubmits.length ? JSON.stringify(recentSubmits, null, 2) : "暂无";
}

async function currentPageUrl() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0]?.url || "";
}

function buildOperatorCommand(pageUrl, captureSessionId) {
  const args = ["python", "-m", "video_orchestrator.cli", "download-page"];
  if (pageUrl) {
    args.push(JSON.stringify(pageUrl));
  } else {
    args.push("<page-url>");
  }
  args.push("--capture-session", JSON.stringify(captureSessionId), "--format", "json");
  return args.join(" ");
}

function renderLatestSummary(payload) {
  const capture = payload.capture || {};
  const rejection = payload.latest_rejection || {};
  latestSummaryEl.textContent = renderLines([
    ["已接受捕获", payload.capture_count || 0],
    ["已拒绝请求", payload.rejection_count || 0],
    ["捕获编号", capture.capture_id],
    ["媒体类别", mediaKindLabel(capture.media_kind)],
    ["候选分数", capture.quality_score],
    ["选择原因", capture.download_candidate_reason],
    ["最新拒绝原因", rejection.reason],
    ["诊断", payload.diagnostic],
  ]);
}

function mediaKindLabel(value) {
  return {
    adaptive: "自适应流",
    audio: "音频",
    mixed: "音视频容器",
    subtitle: "字幕",
    video: "视频",
  }[String(value || "")] || "待识别媒体";
}

function renderHandoffSummary(payload) {
  handoffSummaryEl.textContent = renderLines([
    ["建议工具", payload.recommended_ui?.name],
    ["推荐后端", payload.route?.preferred_backend],
    ["链接类型", payload.route?.kind],
    ["下一步", payload.next_action],
  ]);
  handoffCommand = payload.commands?.page_sniff || payload.commands?.cli_plan || payload.commands?.vsd_capture || "";
}

async function renderOperatorState(captureSessionId) {
  const pageUrl = await currentPageUrl();
  operatorCommand = buildOperatorCommand(pageUrl, captureSessionId);
  operatorCommandEl.textContent = operatorCommand;
  const params = new URLSearchParams({ capture_session_id: captureSessionId });
  if (pageUrl) {
    params.set("page_url", pageUrl);
  }
  try {
    const response = await fetch(`${ORCHESTRATOR_CONFIG.apiBaseUrl}/sniffed-media/latest?${params.toString()}`);
    const payload = await response.json();
    latestStatusEl.textContent = response.ok ? payload.status : `HTTP ${response.status}`;
    latestStatusEl.classList.toggle("ok", response.ok && payload.status === "ok");
    latestStatusEl.classList.toggle("bad", !response.ok);
    renderLatestSummary(payload);
  } catch (error) {
    latestStatusEl.textContent = "离线";
    latestStatusEl.classList.add("bad");
    latestSummaryEl.textContent = JSON.stringify({ error: String(error) }, null, 2);
  }
  await renderHandoffState(pageUrl, captureSessionId);
}

async function renderHandoffState(pageUrl, captureSessionId) {
  if (!pageUrl) {
    handoffSummaryEl.textContent = "暂无当前页链接";
    return;
  }
  try {
    const params = new URLSearchParams({ url: pageUrl, capture_session: captureSessionId });
    const response = await fetch(`${ORCHESTRATOR_CONFIG.apiBaseUrl}/operator/handoff?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw payload;
    }
    renderHandoffSummary(payload);
  } catch (error) {
    handoffSummaryEl.textContent = JSON.stringify({ error: String(error) }, null, 2);
  }
}

function renderOperatorSummary(payload) {
  operatorSummaryEl.textContent = renderLines([
    ["可重试任务", payload.retryable_task_count || 0],
    ["最新捕获状态", payload.latest_capture_status],
    ["最新拒绝原因", payload.latest_rejection?.reason],
    ["自检脚本", payload.smoke_script || "scripts\\operator-smoke.ps1"],
  ]);
}

function renderRetryableTasks(payload, captureSessionId) {
  const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
  const compact = tasks.slice(0, 3);
  retryableTasksEl.textContent = compact.length
    ? compact.map((task) => `#${task.id}｜${task.status}｜${task.backend}\n${task.url}`).join("\n\n")
    : "暂无";
  if (compact.length) {
    resumeCommand = `python -m video_orchestrator.cli task resume ${compact[0].id} --dry-run --capture-session ${JSON.stringify(captureSessionId)} --format json`;
  } else {
    resumeCommand = `python -m video_orchestrator.cli task resume <task-id> --dry-run --capture-session ${JSON.stringify(captureSessionId)} --format json`;
  }
}

async function renderOperatorTaskState(captureSessionId) {
  try {
    const statusResponse = await fetch(`${ORCHESTRATOR_CONFIG.apiBaseUrl}/operator/status`);
    const statusPayload = await statusResponse.json();
    operatorStatusEl.textContent = statusResponse.ok ? statusPayload.status : `HTTP ${statusResponse.status}`;
    operatorStatusEl.classList.toggle("ok", statusResponse.ok && statusPayload.status === "ok");
    operatorStatusEl.classList.toggle("bad", !statusResponse.ok);
    renderOperatorSummary(statusPayload);

    const retryableResponse = await fetch(`${ORCHESTRATOR_CONFIG.apiBaseUrl}/operator/tasks/retryable`);
    const retryablePayload = await retryableResponse.json();
    renderRetryableTasks(retryablePayload, captureSessionId);
  } catch (error) {
    operatorStatusEl.textContent = "离线";
    operatorStatusEl.classList.add("bad");
    operatorSummaryEl.textContent = JSON.stringify({ error: String(error) }, null, 2);
    retryableTasksEl.textContent = "暂无";
  }
}

function newSessionId() {
  return `br-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

async function ensureCaptureSession() {
  const stored = await chrome.storage.local.get(SESSION_KEY);
  const captureSessionId = stored[SESSION_KEY] || newSessionId();
  if (!stored[SESSION_KEY]) {
    await chrome.storage.local.set({ [SESSION_KEY]: captureSessionId });
  }
  captureSessionEl.textContent = captureSessionId;
  return captureSessionId;
}

newSessionEl.addEventListener("click", async () => {
  const captureSessionId = newSessionId();
  await chrome.storage.local.set({ [SESSION_KEY]: captureSessionId });
  captureSessionEl.textContent = captureSessionId;
  await renderOperatorState(captureSessionId);
  await renderOperatorTaskState(captureSessionId);
});

copyCommandEl.addEventListener("click", async () => {
  await navigator.clipboard.writeText(operatorCommand);
  copyCommandEl.textContent = "已复制";
  setTimeout(() => {
    copyCommandEl.textContent = "复制命令";
  }, 1200);
});

copyHandoffCommandEl.addEventListener("click", async () => {
  await navigator.clipboard.writeText(handoffCommand);
  copyHandoffCommandEl.textContent = "已复制";
  setTimeout(() => {
    copyHandoffCommandEl.textContent = "复制建议命令";
  }, 1200);
});

copyRetryableCommandEl.addEventListener("click", async () => {
  await navigator.clipboard.writeText(retryableCommand);
  copyRetryableCommandEl.textContent = "已复制";
  setTimeout(() => {
    copyRetryableCommandEl.textContent = "复制可重试命令";
  }, 1200);
});

copyResumeCommandEl.addEventListener("click", async () => {
  await navigator.clipboard.writeText(resumeCommand);
  copyResumeCommandEl.textContent = "已复制";
  setTimeout(() => {
    copyResumeCommandEl.textContent = "复制恢复命令";
  }, 1200);
});

const captureSessionId = await ensureCaptureSession();
await Promise.all([checkService(), renderOperatorState(captureSessionId), renderOperatorTaskState(captureSessionId), renderLastSubmit()]);
