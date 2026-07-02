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
  --status         Show active job, current stage, and live render phase
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
    --status)       RUN_MODE="status"; shift ;;
    --stop|--down)  RUN_MODE="stop"; shift ;;
    --shutdown)     RUN_MODE="stop"; DO_CLEANUP=true; shift ;;
    --cleanup)      DO_CLEANUP=true; shift ;;
    --reinstall-deps) REINSTALL_DEPS=true; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo -e "${RED}Unknown option: $1${NC}"; usage; exit 2 ;;
  esac
done


if [[ "${RUN_MODE}" == "status" ]]; then
  "${REPO_DIR}/.venv/bin/python" - <<'PY'
import json, sqlite3, sys
from pathlib import Path

root = Path(".")
try:
    conn = sqlite3.connect(root / "jobs" / "queue.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT job_id, status, attempts, started_at FROM job_queue "
        "WHERE status IN ('running','pending') ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
except Exception as exc:
    print(f"queue.db unreadable: {exc}")
    sys.exit(1)
if row is None:
    row = conn.execute(
        "SELECT job_id, status, attempts, started_at FROM job_queue "
        "ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("Queue empty — no jobs yet.")
        sys.exit(0)
job_id = row["job_id"]
print(f"job:    {job_id}")
print(f"queue:  {row['status']} (attempt {row['attempts']}, started {row['started_at']})")
job_dir = root / "jobs" / job_id
try:
    state = json.loads((job_dir / "job.json").read_text())
    stages = state.get("stages", [])
    done = sum(1 for s in stages if s.get("status") == "completed")
    cur = state.get("current_stage")
    print(f"stage:  {cur} ({done}/{len(stages)} completed)")
    for s in stages:
        if s.get("status") in ("failed", "in_progress"):
            err = f" err={s['error']}" if s.get("error") else ""
            print(f"        {s['name']}: {s['status']}{err}")
except Exception:
    print("stage:  job.json unreadable")
prog_path = job_dir / "json" / "render_progress.json"
if prog_path.exists():
    try:
        p = json.loads(prog_path.read_text())
        phase = p.get("phase", "?")
        if p.get("total_frames"):
            print(
                f"render: phase={phase} rendered={p.get('rendered_frame', p.get('frame', 0))}"
                f"/{p['total_frames']} encoded={p.get('encoded_frame', 0)}/{p['total_frames']}"
                f" ({p.get('percent', 0)}%) fps={p.get('fps', 0)} eta={p.get('eta') or '?'}"
            )
        else:
            print(f"render: phase={phase} percent={p.get('percent', 0)}")
    except Exception:
        pass
PY
  exit 0
fi

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

# MeloTTS sidecar venv (Elena voice). Non-fatal: narration falls back to SILENT
# if the channel uses provider "melo" and this venv is absent.
if [[ ! -d "tools/melo-venv" ]]; then
  echo -e "${RED}⚠️  tools/melo-venv missing — Elena (MeloTTS) narration will be SILENT.${NC}"
  echo -e "${CYAN}   Build it once: bash tools/setup-melo-venv.sh${NC}"
fi

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
# HF tokenizers (Grounding DINO / SigLIP) deadlock on their rayon pool after a
# fork when this is unset — encode_batch hangs the worker forever (~0 CPU). The
# Python side also setdefaults this; export here so every launched process is safe.
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export JOBS_DIR="${REPO_DIR}/jobs"
export INPUTS_DIR="${REPO_DIR}/inputs"
export WORKER_ASSETS_ROOT="${REPO_DIR}/jobs"
export BROWSER_TRACE_DIR="${REPO_DIR}/browser_trace"
export CHANNEL_CONFIG="${CHANNEL_CONFIG:-${REPO_DIR}/configs/vida-plena-45/channel.yaml}"
export BROWSER_WORKER_URL="${BROWSER_WORKER_URL:-http://127.0.0.1:8001}"
export CHROME_CDP_URL="${CHROME_CDP_URL:-http://127.0.0.1:9222}"
export PUBLIC_JOBS_KEEP="${PUBLIC_JOBS_KEEP:-5}"
export BROWSER_TRACE_RETENTION_DAYS="${BROWSER_TRACE_RETENTION_DAYS:-3}"
export BROWSER_TRACE_MAX_MB="${BROWSER_TRACE_MAX_MB:-512}"
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"

_actual_listener_pid() {
  # PID actually bound to a TCP port, or empty if nothing is listening.
  # Distinct from a pidfile-recorded PID, which can be alive-but-not-listening
  # (an orphan left over from a bypassed/manual restart) — see reconcile below.
  local port="$1"
  lsof -ti "TCP:${port}" -sTCP:LISTEN 2>/dev/null | head -1
}

_reap_duplicate_processes() {
  # Kill every process matching this exact service command EXCEPT keep_pid.
  # The pidfile-based reconcile in start_proc only ever knows about ONE prior
  # pid (whatever it last wrote) — it can't see orphans left over from manual
  # restarts that bypassed run.sh entirely (nohup uvicorn ... & run directly,
  # e.g. while iterating on a fix). pgrep -f against the full launch command
  # (not just a loose port grep) catches ALL of them without over-matching
  # unrelated processes that merely mention the port number somewhere.
  local keep_pid="$1"
  shift
  local cmd_pattern="$*"
  local pid
  for pid in $(pgrep -f -- "${cmd_pattern}" 2>/dev/null); do
    if [[ "${pid}" != "${keep_pid}" ]]; then
      echo -e "${YELLOW}  reaping orphaned duplicate (pid ${pid}): ${cmd_pattern}${NC}"
      kill "${pid}" 2>/dev/null || true
      sleep 0.3
      kill -9 "${pid}" 2>/dev/null || true
    fi
  done
}

start_proc() {
  local name="$1" port="$2"
  shift 2
  local logfile="logs/${name}.log"
  local pidfile="${PIDFILE_DIR}/${name}.pid"

  if [[ -f "${pidfile}" ]]; then
    local old_pid
    old_pid="$(cat "${pidfile}")"
    if kill -0 "${old_pid}" 2>/dev/null; then
      # For port-bound services, "alive" alone isn't enough — a manual/bypassed
      # restart can leave the OLD pid alive but no longer holding the port
      # (some other pid now listens, or nothing does). Trusting kill -0 alone
      # here previously reported "already running" on a dead, CPU-burning
      # orphan and skipped starting the real replacement.
      if [[ -z "${port}" ]]; then
        echo -e "${GREEN}${name} already running (pid ${old_pid})${NC}"
        return 0
      fi
      local listener
      listener="$(_actual_listener_pid "${port}")"
      if [[ "${listener}" == "${old_pid}" ]]; then
        echo -e "${GREEN}${name} already running (pid ${old_pid}, listening on :${port})${NC}"
        _reap_duplicate_processes "${old_pid}" "$@"
        return 0
      fi
      echo -e "${YELLOW}${name} pidfile (pid ${old_pid}) is alive but not listening on :${port} — stale orphan, stopping it.${NC}"
      kill "${old_pid}" 2>/dev/null || true
      sleep 0.5
      kill -9 "${old_pid}" 2>/dev/null || true
      if [[ -n "${listener}" && "${listener}" != "${old_pid}" ]]; then
        echo -e "${GREEN}${name} already running (pid ${listener}, listening on :${port}) — adopting into pidfile${NC}"
        echo "${listener}" > "${pidfile}"
        _reap_duplicate_processes "${listener}" "$@"
        return 0
      fi
    fi
    rm -f "${pidfile}"
  fi

  echo -e "${CYAN}Starting ${name}...${NC}"
  nohup "$@" >>"${logfile}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" > "${pidfile}"
  echo -e "${GREEN}✅ ${name} pid $(cat "${pidfile}")  log: ${logfile}${NC}"
  if [[ -n "${port}" ]]; then
    sleep 1
    _reap_duplicate_processes "${new_pid}" "$@"
  fi
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

start_proc dashboard 8000 \
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

start_proc browser-worker 8001 \
  uvicorn video_agent.browser_worker.app:app --host 127.0.0.1 --port 8001

start_proc worker "" \
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
