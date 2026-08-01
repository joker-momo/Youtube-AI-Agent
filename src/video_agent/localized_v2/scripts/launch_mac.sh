#!/usr/bin/env bash
# Launch the isolated localized-V2 browser and browser worker on macOS.

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
PROFILE_ROOT="${RUNTIME_ROOT}/browser-profile"
PROCESS_ROOT="${RUNTIME_ROOT}/process"
LOG_ROOT="${RUNTIME_ROOT}/logs"
TRACE_ROOT="${LOG_ROOT}/browser-trace"
READY_TIMEOUT_SEC="${LOCALIZED_V2_READY_TIMEOUT_SEC:-30}"
APP_BUNDLE="${LOCALIZED_V2_BROWSER_APP:-/Applications/Brave Browser.app}"

[[ -x "${VENV_PY}" ]] || { echo "Root venv Python not found: ${VENV_PY}" >&2; exit 1; }
[[ -d "${APP_BUNDLE}" ]] || { echo "Browser app not found: ${APP_BUNDLE}" >&2; exit 1; }
mkdir -p "${PROFILE_ROOT}" "${PROCESS_ROOT}" "${LOG_ROOT}" "${TRACE_ROOT}" "${RUNTIME_ROOT}/work"

listener_pid() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

if [[ -n "$(listener_pid "${CDP_PORT}")" ]]; then
  existing_pid="$(listener_pid "${CDP_PORT}")"
  existing_command="$(ps -p "${existing_pid}" -o command= 2>/dev/null || true)"
  [[ "${existing_command}" == *"--remote-debugging-port=${CDP_PORT}"* && "${existing_command}" == *"--user-data-dir=${PROFILE_ROOT}"* ]] || {
    echo "Port ${CDP_PORT} is owned by a non-V2 process" >&2
    exit 1
  }
else
  open -na "${APP_BUNDLE}" --args \
    --remote-debugging-port="${CDP_PORT}" \
    --user-data-dir="${PROFILE_ROOT}" \
    --no-first-run \
    --no-default-browser-check \
    --disable-features=Translate,MediaRouter \
    >"${LOG_ROOT}/browser.log" 2>&1
fi

for _ in $(seq 1 "${READY_TIMEOUT_SEC}"); do
  browser_pid="$(listener_pid "${CDP_PORT}")"
  if [[ -n "${browser_pid}" ]]; then
    printf '%s\n' "${browser_pid}" > "${PROCESS_ROOT}/browser.pid"
    break
  fi
  sleep 1
done
[[ -s "${PROCESS_ROOT}/browser.pid" ]] || { echo "V2 CDP :${CDP_PORT} did not start" >&2; exit 1; }

if [[ -n "$(listener_pid "${WORKER_PORT}")" ]]; then
  worker_pid="$(listener_pid "${WORKER_PORT}")"
  worker_command="$(ps -p "${worker_pid}" -o command= 2>/dev/null || true)"
  [[ "${worker_command}" == *"video_agent.localized_v2.browser_worker:app"* ]] || {
    echo "Port ${WORKER_PORT} is owned by a non-V2 process" >&2
    exit 1
  }
else
  cd "${WORKTREE_ROOT}"
  /usr/bin/screen -S ybt-localized-v2-browser-worker -X quit 2>/dev/null || true
  /usr/bin/screen -dmS ybt-localized-v2-browser-worker \
    /usr/bin/env LOG_FILE="${LOG_ROOT}/browser-worker.log" \
    /bin/zsh -c 'exec "$@" >> "$LOG_FILE" 2>&1' localized-v2-browser-worker \
    /usr/bin/env \
    PYTHONPATH="${WORKTREE_ROOT}/src" \
    CHROME_CDP_URL="${BROWSER_CDP_URL}" \
    WORKER_ASSETS_ROOT="${RUNTIME_ROOT}" \
    BROWSER_TRACE_DIR="${TRACE_ROOT}" \
    LOCALIZED_V2_BROWSER_PROFILE_ROOT="${PROFILE_ROOT}" \
    LOCALIZED_V2_SESSION_NAMESPACE="localized-v2:en-us" \
    "${VENV_PY}" -m uvicorn \
      video_agent.localized_v2.browser_worker:app \
      --host 127.0.0.1 --port "${WORKER_PORT}"
fi

for _ in $(seq 1 "${READY_TIMEOUT_SEC}"); do
  if health="$(${VENV_PY} -c 'import json,sys,urllib.request; data=json.load(urllib.request.urlopen(sys.argv[1], timeout=2)); assert data == {"ok": True, "service": "localized-v2-browser-worker", "sessionNamespace": "localized-v2:en-us", "profileRoot": sys.argv[2]}; print(json.dumps(data))' "${BROWSER_WORKER_URL}/health" "${PROFILE_ROOT}" 2>/dev/null)"; then
    listener_pid "${WORKER_PORT}" > "${PROCESS_ROOT}/browser-worker.pid"
    echo "Localized V2 browser runtime ready: ${health}"
    exit 0
  fi
  sleep 1
done

echo "V2 browser worker :${WORKER_PORT} failed its identity health check" >&2
exit 1
