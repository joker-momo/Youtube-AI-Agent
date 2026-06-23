#!/usr/bin/env bash
# Native macOS runner. No Docker.
# Starts: dashboard (uvicorn :8000), browser-worker (uvicorn :8001),
# pipeline worker, and Playwright Chromium with CDP on :9222.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

RUN_MODE="full"
REINSTALL_DEPS=false
DO_CLEANUP=false

usage() {
  cat <<EOF
Usage: bash run.sh [--full|--dashboard|--stop|--shutdown] [--cleanup] [--reinstall-deps]

  --full           Start dashboard + browser-worker + worker + Chromium (default)
  --dashboard      Start only the dashboard (port 8000)
  --stop, --down   Stop all native processes
  --shutdown       Stop everything + prune caches (alias: --stop --cleanup)
  --cleanup        Prune logs, __pycache__, old browser traces, npm/pip caches
  --reinstall-deps Reinstall Python + Remotion deps
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)         RUN_MODE="full"; shift ;;
    --dashboard|--app-only) RUN_MODE="dashboard"; shift ;;
    --stop|--down)  RUN_MODE="stop"; shift ;;
    --shutdown)     RUN_MODE="stop"; DO_CLEANUP=true; shift ;;
    --cleanup)      DO_CLEANUP=true; shift ;;
    --reinstall-deps) REINSTALL_DEPS=true; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo -e "${RED}Unknown option: $1${NC}"; usage; exit 2 ;;
  esac
done

# Fast, always-safe rác prune (no log/pip/npm churn). Removes superseded shorts
# archives, stale/_v2 renders, orphaned ai_temp_* image-gen files, empty tmp
# dirs, and HF model variants not referenced by config. Runs on every --full
# launch so disk never silently fills. Products (jobs/, asset_library/) are
# untouched unless JOBS_KEEP is set.
prune_rac() {
  if [[ -d "${REPO_DIR}/jobs" ]]; then
    find "${REPO_DIR}/jobs" -type d \( -name archive -o -name _archive \) -prune -exec rm -rf {} + 2>/dev/null || true
    find "${REPO_DIR}/jobs" -type f \( -name "*stale*" -o -name "*_v2.mp4" -o -name "*_v2.jpg" -o -name "ai_temp_*" \) -delete 2>/dev/null || true
    find "${REPO_DIR}/jobs" -type d -name tmp -empty -delete 2>/dev/null || true
  fi
  # Dead HuggingFace model variants (NOT referenced by channel.yaml config).
  local hf_hub="${HF_HOME:-$HOME/.cache/huggingface}/hub"
  if [[ -d "${hf_hub}" ]]; then
    for dead in "models--mlx-community--Qwen2.5-VL-3B-Instruct-4bit" \
                "models--google--siglip-base-patch16-224" \
                "models--chopratejas--kompress-base" \
                "models--answerdotai--ModernBERT-base"; do
      rm -rf "${hf_hub:?}/${dead}" 2>/dev/null || true
    done
  fi
  # OPT-IN product retention (NEVER deletes by default; products are sacred).
  # JOBS_KEEP=N -> keep N newest job dirs, delete older. Unset = keep all.
  if [[ -n "${JOBS_KEEP:-}" && -d "${REPO_DIR}/jobs" ]]; then
    ls -dt "${REPO_DIR}"/jobs/*/ 2>/dev/null | tail -n +"$((JOBS_KEEP + 1))" | while read -r old; do
      echo -e "${YELLOW}  retention: removing old job $(basename "${old}")${NC}"; rm -rf "${old}"
    done
  fi
}

cleanup_disk() {
  echo -e "${CYAN}Pruning caches...${NC}"
  # Python bytecode — project source only. Scanning the full multi-GB repo
  # (.venv, node_modules, jobs, asset_library) is what made --shutdown crawl;
  # .venv/dependency bytecode is regenerable noise not worth cleaning.
  for _pysrc in src tests scripts; do
    [[ -d "${REPO_DIR}/${_pysrc}" ]] || continue
    find "${REPO_DIR}/${_pysrc}" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "${REPO_DIR}/${_pysrc}" -type f -name "*.pyc" -delete 2>/dev/null || true
  done
  # Logs (truncate, not delete, so file handles survive)
  if [[ -d "${REPO_DIR}/logs" ]]; then
    find "${REPO_DIR}/logs" -type f -name "*.log" -exec sh -c ': > "$1"' _ {} \; 2>/dev/null || true
  fi
  # Browser worker traces older than retention window
  if [[ -d "${REPO_DIR}/browser_trace" ]]; then
    find "${REPO_DIR}/browser_trace" -type f -mtime +"${BROWSER_TRACE_RETENTION_DAYS:-3}" -delete 2>/dev/null || true
  fi
  # Chromium leftover crash dumps + regenerable caches in profile
  if [[ -d "${REPO_DIR}/browser_profiles/default" ]]; then
    find "${REPO_DIR}/browser_profiles/default" -type d \
      \( -name "Crash Reports" -o -name "ShaderCache" -o -name "GrShaderCache" \
         -o -name "GPUCache" -o -name "GraphiteDawnCache" -o -name "Cache" \
         -o -name "Code Cache" -o -name "DawnGraphiteCache" -o -name "DawnWebGPUCache" \) \
      -exec rm -rf {} + 2>/dev/null || true
  fi
  # Remotion render temp + chrome-headless-shell tmpfiles
  rm -rf "${REPO_DIR}/.remotion/tmp" 2>/dev/null || true
  # Always-safe job/model rác + opt-in product retention.
  prune_rac
  # pip + npm cache
  if command -v npm >/dev/null 2>&1; then npm cache clean --force >/dev/null 2>&1 || true; fi
  if [[ -d ".venv" ]]; then .venv/bin/python -m pip cache purge >/dev/null 2>&1 || true; fi
  # macOS DS_Store — prune the giant regenerable/product trees so the scan does
  # not crawl the full repo (same slowdown as the bytecode finds above).
  find "${REPO_DIR}" \
    \( -type d \( -name .venv -o -name node_modules -o -name .git \
       -o -name jobs -o -name asset_library -o -name browser_profiles \
       -o -name browser_trace \) -prune \) \
    -o \( -type f -name ".DS_Store" -delete \) 2>/dev/null || true
  # Force flush filesystem buffers
  sync
  echo -e "${GREEN}✅ Cleanup done.${NC}"
}

mkdir -p logs

PIDFILE_DIR="${REPO_DIR}/.run"
mkdir -p "${PIDFILE_DIR}"

stop_proc() {
  local name="$1"
  local pidfile="${PIDFILE_DIR}/${name}.pid"
  if [[ -f "${pidfile}" ]]; then
    local pid
    pid="$(cat "${pidfile}")"
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      sleep 0.5
      kill -9 "${pid}" 2>/dev/null || true
      echo -e "${YELLOW}Stopped ${name} (pid ${pid})${NC}"
    fi
    rm -f "${pidfile}"
  fi
}

stop_all() {
  stop_proc dashboard
  stop_proc browser-worker
  stop_proc worker
  stop_proc chromium
  pkill -f "uvicorn video_agent.web.app:app" >/dev/null 2>&1 || true
  pkill -f "uvicorn video_agent.browser_worker.app:app" >/dev/null 2>&1 || true
  pkill -f "video_agent.cli worker --db-path jobs/queue.db" >/dev/null 2>&1 || true
  # Local model/render helpers can outlive the queue worker after interrupted
  # Shorts runs. Match project-specific commands only; do not touch normal apps.
  pkill -f "vlm_worker.py" >/dev/null 2>&1 || true
  pkill -f "${REPO_DIR}/remotion" >/dev/null 2>&1 || true
  # Kill the CDP-controlled browser by user-data-dir match so we don't
  # touch the user's normal Brave/Chrome windows.
  pkill -f -- "--user-data-dir=${REPO_DIR}/browser_profiles/default" >/dev/null 2>&1 || true
  sleep 0.5
  pkill -9 -f -- "--user-data-dir=${REPO_DIR}/browser_profiles/default" >/dev/null 2>&1 || true
  # Release singleton lock so next launch starts clean.
  rm -f "${REPO_DIR}/browser_profiles/default/SingletonLock" \
        "${REPO_DIR}/browser_profiles/default/SingletonSocket" \
        "${REPO_DIR}/browser_profiles/default/SingletonCookie" 2>/dev/null || true
}

if [[ "${RUN_MODE}" == "stop" ]]; then
  echo -e "${CYAN}Stopping native services...${NC}"
  stop_all
  echo -e "${GREEN}✅ Stopped.${NC}"
  if [[ "${DO_CLEANUP}" == "true" ]]; then
    cleanup_disk
  fi
  exit 0
fi

if [[ "${DO_CLEANUP}" == "true" ]]; then
  cleanup_disk
fi

if [[ ! -d ".venv" ]]; then
  echo -e "${RED}.venv missing. Run: bash install.sh${NC}"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [[ "${REINSTALL_DEPS}" == "true" ]]; then
  echo -e "${CYAN}Reinstalling Python deps...${NC}"
  python -m pip install --upgrade pip wheel
  python -m pip install -r requirements.txt
  python -m pip install -e .
  echo -e "${CYAN}Reinstalling Remotion...${NC}"
  npm --prefix remotion ci
fi

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
fi
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

export PYTHONPATH="${REPO_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
export JOBS_DIR="${REPO_DIR}/jobs"
export WORKER_ASSETS_ROOT="${REPO_DIR}/jobs"
export BROWSER_TRACE_DIR="${REPO_DIR}/browser_trace"
export CHANNEL_CONFIG="${CHANNEL_CONFIG:-${REPO_DIR}/configs/vida-plena-45/channel.yaml}"
export BROWSER_WORKER_URL="${BROWSER_WORKER_URL:-http://127.0.0.1:8001}"
export CHROME_CDP_URL="${CHROME_CDP_URL:-http://127.0.0.1:9222}"
export PUBLIC_JOBS_KEEP="${PUBLIC_JOBS_KEEP:-5}"
export BROWSER_TRACE_RETENTION_DAYS="${BROWSER_TRACE_RETENTION_DAYS:-3}"
export BROWSER_TRACE_MAX_MB="${BROWSER_TRACE_MAX_MB:-512}"
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"

start_proc() {
  local name="$1"
  shift
  local logfile="logs/${name}.log"
  local pidfile="${PIDFILE_DIR}/${name}.pid"

  if [[ -f "${pidfile}" ]]; then
    local old_pid
    old_pid="$(cat "${pidfile}")"
    if kill -0 "${old_pid}" 2>/dev/null; then
      echo -e "${GREEN}${name} already running (pid ${old_pid})${NC}"
      return 0
    fi
    rm -f "${pidfile}"
  fi

  echo -e "${CYAN}Starting ${name}...${NC}"
  nohup "$@" >>"${logfile}" 2>&1 &
  echo $! > "${pidfile}"
  echo -e "${GREEN}✅ ${name} pid $(cat "${pidfile}")  log: ${logfile}${NC}"
}

wait_for_url() {
  local name="$1" url="$2" timeout="${3:-60}" elapsed=0
  echo -ne "${CYAN}Waiting for ${name}...${NC}"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    sleep 1
    elapsed=$((elapsed + 1))
    echo -ne "."
    if [[ $elapsed -ge $timeout ]]; then
      echo ""
      echo -e "${YELLOW}${name} not ready at ${url} after ${timeout}s. Check log.${NC}"
      return 1
    fi
  done
  echo -e " ${GREEN}ready${NC}"
}

# Auto-prune always-safe rác on every launch so disk never silently fills.
prune_rac

start_proc dashboard \
  uvicorn video_agent.web.app:app --host 127.0.0.1 --port 8000

if [[ "${RUN_MODE}" == "dashboard" ]]; then
  wait_for_url "dashboard" "http://127.0.0.1:8000/health" 60 || true
  echo -e "\n${GREEN}Dashboard:${NC} http://127.0.0.1:8000"
  exit 0
fi

BROWSER="${BROWSER:-brave}"
export BROWSER
if [[ "${BROWSER}" == "playwright" ]] && ! python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); assert p.chromium.executable_path; p.stop()" 2>/dev/null; then
  echo -e "${YELLOW}Playwright Chromium missing — installing...${NC}"
  python -m playwright install chromium
fi
bash "${REPO_DIR}/scripts/launch_chromium_mac.sh" || \
  echo -e "${YELLOW}Chromium launch returned non-zero (may already be running).${NC}"

start_proc browser-worker \
  uvicorn video_agent.browser_worker.app:app --host 127.0.0.1 --port 8001

start_proc worker \
  "${REPO_DIR}/.venv/bin/python" -m video_agent.cli worker --db-path jobs/queue.db

wait_for_url "dashboard" "http://127.0.0.1:8000/health" 60 || true
wait_for_url "browser-worker" "http://127.0.0.1:8001/health" 60 || true

echo ""
echo -e "${BOLD}${GREEN}All services started.${NC}"
echo -e "  Dashboard:       http://127.0.0.1:8000"
echo -e "  Browser worker:  http://127.0.0.1:8001"
echo -e "  Chromium CDP:    http://127.0.0.1:9222"
echo ""
echo -e "Logs:"
echo -e "  tail -f logs/dashboard.log"
echo -e "  tail -f logs/worker.log"
echo -e "  tail -f logs/browser-worker.log"
echo -e "  tail -f logs/chromium.log"
echo ""
echo -e "Stop all: ${BOLD}bash run.sh --stop${NC}"
