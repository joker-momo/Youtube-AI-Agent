#!/usr/bin/env bash
# Start the complete localized-V2 control plane without touching legacy services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
GIT_COMMON_DIR="$(cd "${WORKTREE_ROOT}" && git rev-parse --path-format=absolute --git-common-dir)"
PRIMARY_ROOT="$(dirname "${GIT_COMMON_DIR}")"
VENV_PY="${PRIMARY_ROOT}/.venv/bin/python"
RUNTIME_CONFIG="${LOCALIZED_V2_RUNTIME_CONFIG:-${WORKTREE_ROOT}/configs/localized-v2/runtime.yaml}"
IFS=$'\t' read -r RUNTIME_ROOT DASHBOARD_HOST DASHBOARD_PORT BROWSER_WORKER_URL WORKER_PORT BROWSER_CDP_URL CDP_PORT < <(
  PYTHONPATH="${WORKTREE_ROOT}/src" "${VENV_PY}" -m video_agent.localized_v2.launcher_settings \
    "${RUNTIME_CONFIG}" "${WORKTREE_ROOT}"
)
PROCESS_ROOT="${RUNTIME_ROOT}/process"
LOG_ROOT="${RUNTIME_ROOT}/logs"
READY_TIMEOUT_SEC="${LOCALIZED_V2_READY_TIMEOUT_SEC:-30}"

[[ -x "${VENV_PY}" ]] || { echo "Root venv Python not found: ${VENV_PY}" >&2; exit 1; }
mkdir -p "${PROCESS_ROOT}" "${LOG_ROOT}"
cp "${WORKTREE_ROOT}/configs/localized-v2/capabilities-en-us.yaml" "${RUNTIME_ROOT}/capabilities.yaml"
for clip in intro disclaimer outro; do
  [[ -s "${RUNTIME_ROOT}/media/brand/en-US/${clip}.mp4" ]] || {
    echo "Missing qualified English brand clip: ${clip}.mp4" >&2
    exit 1
  }
done

"${SCRIPT_DIR}/launch_mac.sh"

listener_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

start_python_service() {
  local port="$1"
  local module="$2"
  local pid_file="$3"
  local log_file="$4"
  local marker="$5"
  local screen_name="$6"
  local existing_pid command
  existing_pid="$(listener_pid "${port}")"
  if [[ -n "${existing_pid}" ]]; then
    command="$(ps -p "${existing_pid}" -o command= 2>/dev/null || true)"
    [[ "${command}" == *"${marker}"* ]] || {
      echo "Port ${port} is owned by a non-V2 process" >&2
      exit 1
    }
    printf '%s\n' "${existing_pid}" > "${pid_file}"
    return
  fi
  cd "${WORKTREE_ROOT}"
  /usr/bin/screen -S "${screen_name}" -X quit 2>/dev/null || true
  /usr/bin/screen -dmS "${screen_name}" \
    /usr/bin/env LOG_FILE="${log_file}" \
    /bin/zsh -c 'exec "$@" >> "$LOG_FILE" 2>&1' localized-v2-service \
    /usr/bin/env PYTHONPATH="${WORKTREE_ROOT}/src" LOCALIZED_V2_RUNTIME_CONFIG="${RUNTIME_CONFIG}" \
    "${VENV_PY}" -m "${module}"
}

start_python_service \
  "${DASHBOARD_PORT}" \
  video_agent.localized_v2.dashboard \
  "${PROCESS_ROOT}/dashboard.pid" \
  "${LOG_ROOT}/dashboard.log" \
  video_agent.localized_v2.dashboard \
  ybt-localized-v2-dashboard

# The production worker has no listening socket. Adopt a live V2 worker even
# when a prior launcher was interrupted before it wrote worker.pid.
find_worker_pid() {
  local candidate command process_name
  while IFS= read -r candidate; do
    [[ -n "${candidate}" ]] || continue
    command="$(ps -p "${candidate}" -o command= 2>/dev/null || true)"
    process_name="$(ps -p "${candidate}" -o ucomm= 2>/dev/null | tr -d ' ' || true)"
    if [[ "${process_name}" == [Pp]ython* && "${command}" == *" -m video_agent.localized_v2.production_worker"* ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done < <(pgrep -f "video_agent.localized_v2.production_worker" 2>/dev/null || true)
  return 1
}

screen_session_exists() {
  /usr/bin/screen -ls 2>/dev/null \
    | grep -Eq "[[:space:]][0-9]+\.$1[[:space:]]+\((Detached|Attached)\)"
}

worker_pid="$(find_worker_pid || true)"
if [[ -z "${worker_pid}" ]]; then
  cd "${WORKTREE_ROOT}"
  if ! screen_session_exists ybt-localized-v2-worker; then
    /usr/bin/screen -S ybt-localized-v2-worker -X quit 2>/dev/null || true
    /usr/bin/screen -dmS ybt-localized-v2-worker \
      /usr/bin/env LOG_FILE="${LOG_ROOT}/worker.log" \
      /bin/zsh -c 'exec "$@" >> "$LOG_FILE" 2>&1' localized-v2-worker \
      /usr/bin/env \
      PYTHONPATH="${WORKTREE_ROOT}/src" \
      LOCALIZED_V2_RUNTIME_CONFIG="${RUNTIME_CONFIG}" \
      LOCALIZED_V2_PROVIDER_ENV="${PRIMARY_ROOT}/.env" \
      "${VENV_PY}" -m video_agent.localized_v2.production_worker
  fi
  for _ in $(seq 1 "${READY_TIMEOUT_SEC}"); do
    worker_pid="$(find_worker_pid || true)"
    [[ -n "${worker_pid}" ]] && break
    sleep 1
  done
fi
[[ -n "${worker_pid}" ]] || { echo "Localized V2 production worker did not start" >&2; exit 1; }
printf '%s\n' "${worker_pid}" > "${PROCESS_ROOT}/worker.pid"

for _ in $(seq 1 "${READY_TIMEOUT_SEC}"); do
  if status="$(${VENV_PY} -c 'import json,sys,urllib.request; data=json.load(urllib.request.urlopen(sys.argv[1], timeout=2)); assert data == {"service":"READY","queue":"READY","worker":"ONLINE"}; print(json.dumps(data))' "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/api/v2/health" 2>/dev/null)"; then
    listener_pid "${DASHBOARD_PORT}" > "${PROCESS_ROOT}/dashboard.pid"
    echo "Localized V2 services ready: ${status}"
    echo "Dashboard: http://${DASHBOARD_HOST}:${DASHBOARD_PORT}"
    exit 0
  fi
  sleep 1
done

echo "Localized V2 services failed readiness; inspect ${LOG_ROOT}" >&2
exit 1
