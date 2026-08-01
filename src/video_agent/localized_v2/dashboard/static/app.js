const state = {
  csrfToken: "",
  selectedJobId: new URLSearchParams(window.location.search).get("job"),
  status: new URLSearchParams(window.location.search).get("status") || "",
  jobsRequestVersion: 0,
};

const elements = {
  readiness: document.querySelector("#readiness"),
  createForm: document.querySelector("#create-form"),
  channel: document.querySelector("#channel"),
  topic: document.querySelector("#topic"),
  topicCount: document.querySelector("#topic-count"),
  mutationStatus: document.querySelector("#mutation-status"),
  refreshJobs: document.querySelector("#refresh-jobs"),
  statusFilter: document.querySelector("#status-filter"),
  jobList: document.querySelector("#job-list"),
  detailEmpty: document.querySelector("#detail-empty"),
  detailContent: document.querySelector("#detail-content"),
  detailSummary: document.querySelector("#detail-summary"),
  actionBar: document.querySelector("#action-bar"),
  eventList: document.querySelector("#event-list"),
  artifactList: document.querySelector("#artifact-list"),
};

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
      }),
    });
    elements.topic.value = "";
    elements.topicCount.textContent = "0 / 240";
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
