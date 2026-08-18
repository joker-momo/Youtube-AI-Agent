#!/usr/bin/env bash
# Stop only processes whose recorded command line proves V2 ownership.

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
PROFILE_ROOT="${RUNTIME_ROOT}/browser-profile"

stop_owned() {
  local pid_file="$1"
  local marker="$2"
  [[ -f "${pid_file}" ]] || return 0
  local pid command
  pid="$(tr -cd '0-9' < "${pid_file}")"
  [[ -n "${pid}" ]] || { rm -f "${pid_file}"; return 0; }
  command="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
  if [[ -n "${command}" && "${command}" == *"${marker}"* ]]; then
    kill "${pid}"
  fi
  rm -f "${pid_file}"
}

stop_owned "${PROCESS_ROOT}/browser-worker.pid" "video_agent.localized_v2.browser_worker:app"
stop_owned "${PROCESS_ROOT}/dashboard.pid" "video_agent.localized_v2.dashboard"
stop_owned "${PROCESS_ROOT}/worker.pid" "video_agent.localized_v2.production_worker"
/usr/bin/screen -S ybt-localized-v2-browser-worker -X quit 2>/dev/null || true
/usr/bin/screen -S ybt-localized-v2-dashboard -X quit 2>/dev/null || true
/usr/bin/screen -S ybt-localized-v2-worker -X quit 2>/dev/null || true
stop_owned "${PROCESS_ROOT}/browser.pid" "--user-data-dir=${PROFILE_ROOT}"
