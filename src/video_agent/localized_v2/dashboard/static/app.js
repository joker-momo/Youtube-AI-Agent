const state = {
  csrfToken: "",
  selectedJobId: new URLSearchParams(window.location.search).get("job"),
  status: new URLSearchParams(window.location.search).get("status") || "",
  jobsRequestVersion: 0,
};

const PIPELINE_PHASES = [
  {
    id: "content",
    label: "Content",
    stages: [
      {id: "idea", label: "Idea"},
      {id: "script", label: "Script"},
      {id: "scenes", label: "Scenes"},
      {id: "seo", label: "SEO"},
      {id: "qa", label: "QA"},
    ],
  },
  {
    id: "voice",
    label: "Voice",
    stages: [
      {id: "audio", label: "Narration"},
      {id: "timing", label: "Timing"},
    ],
  },
  {
    id: "media",
    label: "Media",
    stages: [{id: "assets", label: "Assets"}],
  },
  {
    id: "assembly",
    label: "Assembly",
    stages: [
      {id: "branding", label: "Branding"},
      {id: "render_props", label: "Render setup"},
    ],
  },
  {
    id: "render",
    label: "Render",
    stages: [{id: "render", label: "Final video"}],
  },
];

const PIPELINE_STAGES = PIPELINE_PHASES.flatMap((phase) => phase.stages);

const elements = {
  readiness: document.querySelector("#readiness"),
  createForm: document.querySelector("#create-form"),
  channel: document.querySelector("#channel"),
  topic: document.querySelector("#topic"),
  topicCount: document.querySelector("#topic-count"),
  description: document.querySelector("#description"),
  descriptionCount: document.querySelector("#description-count"),
  mutationStatus: document.querySelector("#mutation-status"),
  refreshJobs: document.querySelector("#refresh-jobs"),
  statusFilter: document.querySelector("#status-filter"),
  jobList: document.querySelector("#job-list"),
  detailEmpty: document.querySelector("#detail-empty"),
  detailContent: document.querySelector("#detail-content"),
  detailSummary: document.querySelector("#detail-summary"),
  actionBar: document.querySelector("#action-bar"),
  overallProgress: document.querySelector("#overall-progress"),
  overallProgressFill: document.querySelector("#overall-progress-fill"),
  pipelinePercent: document.querySelector("#pipeline-percent"),
  pipelineSummary: document.querySelector("#pipeline-summary"),
  phaseList: document.querySelector("#phase-list"),
  stageList: document.querySelector("#stage-list"),
  eventList: document.querySelector("#event-list"),
  artifactList: document.querySelector("#artifact-list"),
};

function parseTime(value) {
  const time = Date.parse(value || "");
  return Number.isNaN(time) ? null : time;
}

function formatDuration(startedAt, endedAt) {
  const start = parseTime(startedAt);
  if (start === null) return "Waiting";
  const end = parseTime(endedAt) ?? Date.now();
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function eventFailureText(event) {
  const payload = event.payload || {};
  return payload.message || payload.detail || payload.code || "Stage failed";
}

function buildStageStates(job, events) {
  const stages = Object.fromEntries(
    PIPELINE_STAGES.map((stage) => [
      stage.id,
      {...stage, status: "pending", startedAt: null, endedAt: null},
    ]),
  );
  [...events]
    .sort((left, right) => (left.sequence || 0) - (right.sequence || 0))
    .forEach((event) => {
      const stageId = event.stage || event.payload?.stage;
      const stage = stages[stageId];
      if (!stage) return;
      if (event.type === "STAGE_STARTED") {
        stage.status = "running";
        stage.startedAt = event.createdAt;
        stage.endedAt = null;
        delete stage.error;
      } else if (event.type === "STAGE_COMPLETED") {
        stage.status = "completed";
        stage.startedAt ||= event.createdAt;
        stage.endedAt = event.createdAt;
        delete stage.error;
      } else if (event.type === "FAILED") {
        stage.status = "failed";
        stage.startedAt ||= event.createdAt;
        stage.endedAt = event.createdAt;
        stage.error = eventFailureText(event);
      }
    });

  const currentStage = stages[job.currentStage];
  if (
    currentStage &&
    ["RUNNING", "CANCEL_REQUESTED"].includes(job.status) &&
    currentStage.status === "pending"
  ) {
    currentStage.status = "running";
    currentStage.startedAt = job.updatedAt;
  }
  if (job.status === "FAILED") {
    const failedStage = stages[job.failure?.stage || job.currentStage];
    if (failedStage && failedStage.status !== "completed") {
      failedStage.status = "failed";
      failedStage.startedAt ||= job.updatedAt;
      failedStage.endedAt ||= job.updatedAt;
      failedStage.error ||= job.failure?.message || job.failure?.code || "Stage failed";
    }
  }
  return stages;
}

function phaseStatus(phase, stages) {
  const statuses = phase.stages.map((stage) => stages[stage.id].status);
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("running")) return "running";
  if (statuses.every((status) => status === "completed")) return "completed";
  return "pending";
}

function statusText(status) {
  return {
    pending: "Waiting",
    running: "In progress",
    completed: "Complete",
    failed: "Needs attention",
  }[status];
}

function renderPipeline(job, events) {
  const stages = buildStageStates(job, events);
  const completedCount = Object.values(stages).filter(
    (stage) => stage.status === "completed",
  ).length;
  const percent = Math.round((completedCount / PIPELINE_STAGES.length) * 100);
  elements.overallProgress.setAttribute("aria-valuenow", String(percent));
  elements.overallProgress.setAttribute(
    "aria-valuetext",
    `${completedCount} of ${PIPELINE_STAGES.length} steps complete`,
  );
  elements.overallProgressFill.style.width = `${percent}%`;
  elements.pipelinePercent.textContent = `${percent}%`;
  elements.pipelineSummary.textContent = `${completedCount} of ${PIPELINE_STAGES.length} steps complete`;

  replaceChildren(
    elements.phaseList,
    PIPELINE_PHASES.map((phase, index) => {
      const status = phaseStatus(phase, stages);
      const item = document.createElement("div");
      item.className = "phase-card";
      item.setAttribute("role", "listitem");
      item.dataset.phaseId = phase.id;
      item.dataset.status = status;
      item.append(
        textElement("span", "phase-index", String(index + 1).padStart(2, "0")),
        textElement("strong", "phase-label", phase.label),
        textElement("span", "phase-status", statusText(status)),
      );
      return item;
    }),
  );

  replaceChildren(
    elements.stageList,
    PIPELINE_STAGES.map((definition, index) => {
      const stage = stages[definition.id];
      const item = document.createElement("li");
      item.className = "stage-card";
      item.dataset.stageId = stage.id;
      item.dataset.status = stage.status;
      const marker = textElement(
        "span",
        "stage-marker",
        stage.status === "completed" ? "✓" : String(index + 1),
      );
      marker.setAttribute("aria-hidden", "true");
      const copy = document.createElement("div");
      copy.className = "stage-copy";
      copy.append(
        textElement("strong", "stage-label", stage.label),
        textElement("span", "stage-status", statusText(stage.status)),
      );
      const meta = textElement(
        "span",
        "stage-duration",
        formatDuration(stage.startedAt, stage.endedAt),
      );
      item.append(marker, copy, meta);
      if (stage.error) item.append(textElement("p", "stage-error", stage.error));
      return item;
    }),
  );
}

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function replaceChildren(element, children) {
  element.replaceChildren(...children);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", state.csrfToken);
  }
  const response = await fetch(path, {...options, headers});
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
    error.code = payload?.error?.code || "REQUEST_FAILED";
    throw error;
  }
  return payload;
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.selectedJobId) params.set("job", state.selectedJobId);
  if (state.status) params.set("status", state.status);
  const query = params.toString();
  history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function statusLabel(status) {
  return status.toLowerCase().replaceAll("_", " ");
}

function renderReadiness(health) {
  const worker = health.worker === "ONLINE" ? "Worker online" : "Worker offline";
  elements.readiness.textContent = `${health.service} · Queue ${health.queue.toLowerCase()} · ${worker}`;
  elements.readiness.dataset.worker = health.worker;
}

function renderJobs(jobs) {
  elements.jobList.setAttribute("aria-busy", "false");
  if (!jobs.length) {
    replaceChildren(
      elements.jobList,
      [textElement("p", "empty-state", "No localized V2 jobs match this filter.")],
    );
    return;
  }
  const rows = jobs.map((job) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "job-row";
    button.dataset.selected = String(job.jobId === state.selectedJobId);
    button.setAttribute("role", "listitem");
    button.append(
      textElement("span", "job-topic", job.topic),
      textElement("span", "job-meta", `${job.locale} · ${statusLabel(job.status)}`),
      textElement("span", "job-time", new Date(job.createdAt).toLocaleString()),
    );
    button.addEventListener("click", () => selectJob(job.jobId));
    return button;
  });
  replaceChildren(elements.jobList, rows);
}

function detailLine(label, value) {
  const row = document.createElement("div");
  row.className = "detail-line";
  row.append(textElement("dt", "", label), textElement("dd", "", value || "—"));
  return row;
}

function actionFor(job, label, resource) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", async () => {
    button.disabled = true;
    elements.mutationStatus.textContent = `${label} pending…`;
    try {
      await api(`/api/v2/jobs/${job.jobId}/${resource}`, {method: "POST"});
      elements.mutationStatus.textContent = `${label} accepted.`;
      await Promise.all([loadJobs(), loadDetail(job.jobId)]);
    } catch (error) {
      elements.mutationStatus.textContent = `${error.code}: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function renderActions(job) {
  const actions = [];
  if (["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(job.status)) {
    actions.push(actionFor(job, "Cancel job", "cancellation"));
  } else if (job.status === "FAILED") {
    actions.push(actionFor(job, "Retry job", "retry-attempts"));
  } else if (job.status === "INTERRUPTED") {
    actions.push(actionFor(job, "Resume job", "resume-attempts"));
  }
  if (!actions.length) {
    actions.push(textElement("p", "terminal-note", `This job is ${statusLabel(job.status)}.`));
  }
  replaceChildren(elements.actionBar, actions);
}

function renderEvents(events) {
  if (!events.length) {
    replaceChildren(elements.eventList, [textElement("li", "empty-state", "No events yet.")]);
    return;
  }
  replaceChildren(
    elements.eventList,
    events.map((event) => {
      const item = document.createElement("li");
      item.append(
        textElement("strong", "", event.type.replaceAll("_", " ")),
        textElement(
          "span",
          "",
          `${event.stage ? ` · ${event.stage}` : ""} · ${new Date(event.createdAt).toLocaleString()}`,
        ),
      );
      return item;
    }),
  );
}

function renderArtifacts(artifacts) {
  if (!artifacts.length) {
    replaceChildren(
      elements.artifactList,
      [textElement("li", "empty-state", "No promoted artifacts yet.")],
    );
    return;
  }
  replaceChildren(
    elements.artifactList,
    artifacts.map((artifact) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = artifact.downloadUrl;
      link.textContent = artifact.name;
      item.append(link, textElement("span", "", ` · ${artifact.stage}`));
      return item;
    }),
  );
}

function clearDetail() {
  elements.detailEmpty.hidden = false;
  elements.detailContent.hidden = true;
  replaceChildren(elements.detailSummary, []);
  replaceChildren(elements.actionBar, []);
  elements.overallProgress.setAttribute("aria-valuenow", "0");
  elements.overallProgress.setAttribute(
    "aria-valuetext",
    `0 of ${PIPELINE_STAGES.length} steps complete`,
  );
  elements.overallProgressFill.style.width = "0%";
  elements.pipelinePercent.textContent = "0%";
  elements.pipelineSummary.textContent = `0 of ${PIPELINE_STAGES.length} steps complete`;
  replaceChildren(elements.phaseList, []);
  replaceChildren(elements.stageList, []);
  replaceChildren(elements.eventList, []);
  replaceChildren(elements.artifactList, []);
}

async function loadDetail(jobId) {
  const [job, events, artifacts] = await Promise.all([
    api(`/api/v2/jobs/${jobId}`),
    api(`/api/v2/jobs/${jobId}/events`),
    api(`/api/v2/jobs/${jobId}/artifacts`),
  ]);
  if (jobId !== state.selectedJobId) return;
  elements.detailEmpty.hidden = true;
  elements.detailContent.hidden = false;
  const description = document.createElement("dl");
  description.append(
    detailLine("Topic", job.topic),
    detailLine("Description", job.description),
    detailLine("Status", statusLabel(job.status)),
    detailLine("Locale", job.locale),
    detailLine("Current stage", job.currentStage),
    detailLine("Attempt", String(job.attemptCount)),
    detailLine("Created", new Date(job.createdAt).toLocaleString()),
    detailLine("Updated", new Date(job.updatedAt).toLocaleString()),
  );
  replaceChildren(
    elements.detailSummary,
    [
      textElement("p", "detail-id", job.jobId),
      textElement("h3", "detail-topic", job.topic),
      description,
    ],
  );
  renderActions(job);
  renderPipeline(job, events.data);
  renderEvents(events.data);
  renderArtifacts(artifacts.data);
}

async function selectJob(jobId) {
  state.selectedJobId = jobId;
  updateUrl();
  await Promise.all([loadJobs(), loadDetail(jobId)]);
}

async function loadJobs() {
  const requestVersion = ++state.jobsRequestVersion;
  const requestedStatus = state.status;
  elements.jobList.setAttribute("aria-busy", "true");
  const statusQuery = requestedStatus ? `&status=${encodeURIComponent(requestedStatus)}` : "";
  const payload = await api(`/api/v2/jobs?page=1&pageSize=50${statusQuery}`);
  if (requestVersion !== state.jobsRequestVersion || requestedStatus !== state.status) {
    return state.selectedJobId;
  }
  const selectionIsVisible = payload.data.some(
    (job) => job.jobId === state.selectedJobId,
  );
  if (!selectionIsVisible) {
    state.selectedJobId = payload.data[0]?.jobId || null;
    updateUrl();
  }
  renderJobs(payload.data);
  return state.selectedJobId;
}

async function refreshDashboard() {
  const selectedJobId = await loadJobs();
  if (selectedJobId) {
    await loadDetail(selectedJobId);
  } else {
    clearDetail();
  }
}

async function loadChannels() {
  const payload = await api("/api/v2/channels");
  const options = payload.data.map((channel) => {
    const option = document.createElement("option");
    option.value = channel.channelId;
    const label = channel.mode === "qualification" ? "CANARY" : "PRODUCTION";
    option.textContent = `${channel.name} (${channel.locale}) — ${label}`;
    return option;
  });
  if (!options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No production or canary channels available";
    options.push(option);
    elements.channel.disabled = true;
  }
  replaceChildren(elements.channel, options);
}

elements.topic.addEventListener("input", () => {
  elements.topicCount.textContent = `${elements.topic.value.length} / 240`;
});

elements.description.addEventListener("input", () => {
  elements.descriptionCount.textContent = `${elements.description.value.length} / 2000`;
});

elements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = elements.createForm.querySelector("button[type='submit']");
  submit.disabled = true;
  elements.mutationStatus.textContent = "Creating queued job…";
  try {
    const job = await api("/api/v2/jobs", {
      method: "POST",
      body: JSON.stringify({
        channelId: elements.channel.value,
        topic: elements.topic.value,
        description: elements.description.value,
      }),
    });
    elements.topic.value = "";
    elements.topicCount.textContent = "0 / 240";
    elements.description.value = "";
    elements.descriptionCount.textContent = "0 / 2000";
    elements.mutationStatus.textContent = "Queued successfully.";
    await selectJob(job.jobId);
  } catch (error) {
    elements.mutationStatus.textContent = `${error.code}: ${error.message}`;
  } finally {
    submit.disabled = false;
  }
});

elements.refreshJobs.addEventListener("click", () => refreshDashboard());
elements.statusFilter.value = state.status;
elements.statusFilter.addEventListener("change", async () => {
  state.status = elements.statusFilter.value;
  state.selectedJobId = null;
  updateUrl();
  await refreshDashboard();
});

async function initialize() {
  try {
    const session = await api("/api/v2/session");
    state.csrfToken = session.csrfToken;
    const [health] = await Promise.all([api("/api/v2/health"), loadChannels()]);
    renderReadiness(health);
    await refreshDashboard();
  } catch (error) {
    elements.readiness.textContent = `V2 service unavailable: ${error.message}`;
    elements.jobList.setAttribute("aria-busy", "false");
    replaceChildren(
      elements.jobList,
      [textElement("p", "empty-state error-state", "Could not load localized V2 jobs.")],
    );
  }
}

initialize();
window.setInterval(async () => {
  try {
    renderReadiness(await api("/api/v2/health"));
    await refreshDashboard();
  } catch {
    elements.readiness.textContent = "Localized V2 service temporarily unavailable";
  }
}, 5000);
