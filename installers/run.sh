#!/usr/bin/env bash
# Runner script for YouTube AI Agent
# Starts Docker if needed, verifies environment, and launches all containers.

set -euo pipefail

# Text formatting helper constants
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Print banner
echo -e "${BOLD}${BLUE}=====================================================${NC}"
echo -e "${BOLD}${BLUE}          YouTube AI Agent Launcher                  ${NC}"
echo -e "${BOLD}${BLUE}=====================================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

WORKER_MODE="auto"
RUN_MODE="full"
RUN_CLEANUP=false
REINSTALL_DEPS=false
STOP_HEAVY=false

usage() {
  echo "Usage: bash run.sh [--dashboard|--app-only|--full|--stop-heavy|--stop] [--cleanup] [--reinstall-deps] [--native-worker|--docker-worker]"
  echo ""
  echo "Mac default: full stack with native host worker, no prompt."
  echo "Linux default: full stack with Docker worker."
  echo ""
  echo "Modes:"
  echo "  --dashboard, --app-only  Start only the dashboard container."
  echo "  --full                  Start dashboard + browser services + worker."
  echo "  --stop-heavy            Stop worker/browser services and keep dashboard available."
  echo "  --stop, --down          Stop all services (containers and native worker)."
  echo ""
  echo "Options:"
  echo "  --cleanup               Prune Docker cache and local browser/public debug artifacts first."
  echo "  --reinstall-deps        Reinstall native Python/Remotion dependencies."
  echo "  --native-worker         Force native host worker on macOS."
  echo "  --docker-worker         Force Docker worker."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard)
      RUN_MODE="dashboard"
      shift
      ;;
    --app-only)
      RUN_MODE="dashboard"
      shift
      ;;
    --full)
      RUN_MODE="full"
      shift
      ;;
    --stop-heavy)
      STOP_HEAVY=true
      RUN_MODE="dashboard"
      shift
      ;;
    --stop|--down)
      RUN_MODE="stop"
      shift
      ;;
    --cleanup)
      RUN_CLEANUP=true
      shift
      ;;
    --reinstall-deps)
      REINSTALL_DEPS=true
      shift
      ;;
    --native-worker)
      WORKER_MODE="native"
      shift
      ;;
    --docker-worker)
      WORKER_MODE="docker"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      usage
      exit 2
      ;;
  esac
done

export PUBLIC_JOBS_KEEP="${PUBLIC_JOBS_KEEP:-5}"
export BROWSER_TRACE_RETENTION_DAYS="${BROWSER_TRACE_RETENTION_DAYS:-3}"
export BROWSER_TRACE_MAX_MB="${BROWSER_TRACE_MAX_MB:-512}"

# 1. Check Docker Daemon
if [[ "$RUN_MODE" != "stop" ]]; then
  echo -e "${CYAN}Checking Docker status...${NC}"
  if ! docker info &>/dev/null; then
    # Detect OS
    OS="$(uname -s)"
    if [[ "$OS" == "Darwin" ]]; then
      echo -e "${YELLOW}Docker is not running. Launching Docker Desktop on macOS...${NC}"
      open -a Docker
    elif [[ "$OS" == "Linux" ]]; then
      echo -e "${YELLOW}Docker is not running. Attempting to start service on Linux...${NC}"
      sudo systemctl start docker
    fi

    echo -e "${BLUE}Waiting for Docker to start...${NC}"
    count=0
    until docker info &>/dev/null; do
      echo -ne "."
      sleep 3
      count=$((count + 3))
      if [[ $count -ge 60 ]]; then
        echo ""
        echo -e "${RED}❌ Docker took too long to start. Please start Docker manually and run this script again.${NC}"
        exit 1
      fi
    done
    echo ""
  fi
  echo -e "${GREEN}✅ Docker is active and running!${NC}\n"
fi

if [ "$RUN_CLEANUP" = true ]; then
  echo -e "${CYAN}Running Docker/local artifact cleanup...${NC}"
  bash scripts/docker_disk_cleanup.sh
fi

stop_native_worker() {
  pkill -f "video_agent.cli worker --db-path jobs/queue.db" >/dev/null 2>&1 || true
}

stop_heavy_services() {
  docker compose ${COMPOSE_ARGS:-"-f docker-compose.yml"} stop worker browser-worker browser-runtime >/dev/null 2>&1 || true
  stop_native_worker
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local timeout_sec="${3:-60}"
  local elapsed=0

  if ! command -v curl &>/dev/null; then
    echo -e "${YELLOW}curl is not available; skipping ${name} readiness check.${NC}"
    return 0
  fi

  echo -ne "${CYAN}Waiting for ${name}...${NC}"
  until curl -fsS "${url}" >/dev/null 2>&1; do
    sleep 2
    elapsed=$((elapsed + 2))
    echo -ne "."
    if [[ $elapsed -ge $timeout_sec ]]; then
      echo ""
      echo -e "${RED}${name} did not become ready at ${url} within ${timeout_sec}s.${NC}"
      return 1
    fi
  done
  echo -e " ${GREEN}ready${NC}"
}

# 2. Check for .env file
if [[ ! -f ".env" ]]; then
  echo -e "${YELLOW}⚠️  .env file not found. Copying from .env.example...${NC}"
  cp .env.example .env
fi

# 3. Detect hardware and configure Docker Compose
COMPOSE_ARGS="-f docker-compose.yml"
HAS_GPU=false
GPU_TYPE=""
OS="$(uname -s)"

if [[ "$OS" == "Linux" ]]; then
  # Check for NVIDIA GPU
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    HAS_GPU=true
    GPU_TYPE="NVIDIA"
    COMPOSE_ARGS="${COMPOSE_ARGS} -f docker-compose.nvidia.yml"
  # Check for AMD GPU on Linux
  elif [[ -e /dev/dri ]] && command -v lspci &>/dev/null && lspci | grep -qi "amd\|ati\|radeon"; then
    HAS_GPU=true
    GPU_TYPE="AMD"
    COMPOSE_ARGS="${COMPOSE_ARGS} -f docker-compose.amd.yml"
  fi
fi

# Print GPU status
if [ "$HAS_GPU" = true ]; then
  echo -e "${GREEN}✅ Detected ${GPU_TYPE} GPU! Configuring Docker for GPU acceleration...${NC}"
elif [[ "$OS" == "Darwin" ]]; then
  echo -e "${BLUE}ℹ️  macOS detected. Native host worker is preferred for render performance.${NC}"
else
  echo -e "${BLUE}ℹ️  No compatible Linux GPU detected. Running in standard CPU mode.${NC}"
fi

if [[ "$RUN_MODE" == "stop" ]]; then
  echo -e "${CYAN}Stopping all services...${NC}"
  if docker info &>/dev/null; then
    docker compose ${COMPOSE_ARGS} down
  else
    echo -e "${YELLOW}Docker is not running; skipping container shutdown.${NC}"
  fi
  stop_native_worker
  echo -e "${GREEN}✅ All services stopped successfully.${NC}"
  exit 0
fi

# 4. Handle macOS Native Worker Speedup Option
USE_NATIVE_WORKER=false
if [[ "$OS" == "Darwin" ]]; then
  echo -e "\n${BOLD}${YELLOW}================ macOS Performance Optimization =================${NC}"
  echo -e "Docker on macOS runs inside a Linux VM and cannot use your Mac's hardware GPU/VideoToolbox."
  echo -e "Running the Worker natively on your Mac provides a ${BOLD}3x+ render speedup${NC}."
  if [[ "$RUN_MODE" == "dashboard" ]]; then
    echo -e "${BLUE}Dashboard-only mode requested; worker/browser services will stay stopped.${NC}"
  elif [[ "$WORKER_MODE" == "docker" ]]; then
    echo -e "${BLUE}Docker worker requested via --docker-worker.${NC}"
  elif [[ "$WORKER_MODE" == "native" ]]; then
    echo -e "${GREEN}Native worker requested via --native-worker.${NC}"
    USE_NATIVE_WORKER=true
  else
    echo -e "Mac-first mode: Webapp/Browser stay in Docker, Worker runs natively on host Mac."
    USE_NATIVE_WORKER=true
  fi
  echo -e "${BOLD}${YELLOW}=================================================================${NC}\n"
elif [[ "$WORKER_MODE" == "native" ]]; then
  echo -e "${YELLOW}--native-worker is currently only supported on macOS. Falling back to Docker worker.${NC}"
fi

if [ "$STOP_HEAVY" = true ]; then
  echo -e "${CYAN}Stopping worker/browser services and keeping dashboard mode...${NC}"
  stop_heavy_services
fi

if [[ "$RUN_MODE" == "dashboard" ]]; then
  echo -e "${CYAN}Starting dashboard only in Docker...${NC}"
  docker compose ${COMPOSE_ARGS} up -d app
  stop_heavy_services
elif [ "$USE_NATIVE_WORKER" = true ]; then
  # Find best python command (prefer homebrew python3.11/3.12/3.10 over older system python3)
  PYTHON_CMD="python3"
  if command -v python3.11 &>/dev/null; then
    PYTHON_CMD="python3.11"
  elif command -v python3.12 &>/dev/null; then
    PYTHON_CMD="python3.12"
  elif command -v python3.10 &>/dev/null; then
    PYTHON_CMD="python3.10"
  fi

  echo -e "${CYAN}Using Python interpreter: ${PYTHON_CMD}${NC}"

  if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
    echo -e "${RED}Native worker needs Node.js and npm on the host for Remotion render.${NC}"
    echo -e "${YELLOW}Install Node.js 22+, or run with --docker-worker.${NC}"
    exit 1
  fi

  # If .venv exists, verify its python version is >= 3.10
  if [[ -d ".venv" ]]; then
    IS_COMPATIBLE=$(.venv/bin/python -c "import sys; print(sys.version_info >= (3, 10))" 2>/dev/null || echo "False")
    if [[ "$IS_COMPATIBLE" == "False" ]]; then
      echo -e "${YELLOW}Existing .venv is using an incompatible Python version. Recreating...${NC}"
      rm -rf .venv
    fi
  fi

  echo -e "${CYAN}Checking native Python virtual environment on host Mac...${NC}"
  if [[ ! -d ".venv" ]]; then
    $PYTHON_CMD -m venv .venv
  fi
  source .venv/bin/activate
  DEPS_MARKER=".venv/.requirements-installed"
  if [ "$REINSTALL_DEPS" = true ] || [[ ! -f "$DEPS_MARKER" ]] || [[ "requirements.txt" -nt "$DEPS_MARKER" ]]; then
    echo -e "${BLUE}Installing Python dependencies (this might take a minute)...${NC}"
    python -m pip install --quiet --timeout 100 --retries 10 --upgrade pip
    python -m pip install --quiet --timeout 100 --retries 10 -r requirements.txt
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$DEPS_MARKER"
  else
    echo -e "${GREEN}Native Python dependencies are up to date.${NC}"
  fi

  echo -e "${CYAN}Checking Remotion dependencies on host Mac...${NC}"
  REMOTION_MARKER="remotion/node_modules/.package-lock-installed"
  if [ "$REINSTALL_DEPS" = true ] || [[ ! -x "remotion/node_modules/.bin/remotion" ]] || [[ "remotion/package-lock.json" -nt "$REMOTION_MARKER" ]]; then
    echo -e "${BLUE}Installing Remotion dependencies in remotion/node_modules...${NC}"
    npm --prefix remotion ci --quiet
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$REMOTION_MARKER"
  else
    echo -e "${GREEN}Remotion dependencies are up to date.${NC}"
  fi
  if [[ ! -d ".remotion/chrome-headless-shell" ]]; then
    echo -e "${BLUE}Ensuring Remotion browser is available...${NC}"
    npx --prefix remotion remotion browser ensure
  else
    echo -e "${GREEN}Remotion browser is already available.${NC}"
  fi
  
  # Start app and browser containers in Docker, but exclude the docker worker
  echo -e "${CYAN}Starting App & Browser containers in Docker...${NC}"
  docker compose ${COMPOSE_ARGS} up -d app browser-worker browser-runtime
  
  # Ensure the docker worker is stopped to prevent conflicts
  docker compose ${COMPOSE_ARGS} stop worker &>/dev/null || true
  
  # Run the native worker in the background
  mkdir -p logs
  if pgrep -f "video_agent.cli worker --db-path jobs/queue.db" >/dev/null; then
    echo -e "${YELLOW}Native Worker already appears to be running. Keeping existing process.${NC}"
    echo -e "${YELLOW}Logs may still be in logs/native_worker.log.${NC}"
  else
    echo -e "${CYAN}Launching Native Worker on host Mac (3x speedup)...${NC}"
    nohup env \
      PYTHONPATH=src \
      JOBS_DIR="${REPO_DIR}/jobs" \
      CHANNEL_CONFIG="${REPO_DIR}/configs/vida-plena-45/channel.yaml" \
      BROWSER_WORKER_URL="http://localhost:8001" \
      PUBLIC_JOBS_KEEP="${PUBLIC_JOBS_KEEP}" \
      BROWSER_TRACE_RETENTION_DAYS="${BROWSER_TRACE_RETENTION_DAYS}" \
      BROWSER_TRACE_MAX_MB="${BROWSER_TRACE_MAX_MB}" \
      python -m video_agent.cli worker --db-path jobs/queue.db > logs/native_worker.log 2>&1 &
    echo -e "${GREEN}✅ Native Worker started in the background (PID: $!). Logs: logs/native_worker.log${NC}"
  fi
else
  # Standard Docker startup
  echo -e "${CYAN}Starting YouTube AI Agent services in Docker...${NC}"
  docker compose ${COMPOSE_ARGS} up -d app worker browser-worker browser-runtime
fi

# 5. Check Health
wait_for_url "web app" "http://localhost:8000/health" 90
if [[ "$RUN_MODE" != "dashboard" ]]; then
  wait_for_url "browser-worker" "http://localhost:8001/health" 90
  wait_for_url "browser runtime CDP bridge" "http://localhost:8001/runtime" 120
fi

echo -e "\n${GREEN}✅ Services started successfully!${NC}"
echo -e "${BOLD}${CYAN}=====================================================${NC}"
echo -e "  - ${BOLD}Dashboard URL:${NC}      ${GREEN}http://localhost:8000${NC}"
if [[ "$RUN_MODE" == "dashboard" ]]; then
  echo -e "  - ${BOLD}Worker Status:${NC}      ${YELLOW}STOPPED (dashboard mode)${NC}"
elif [ "$USE_NATIVE_WORKER" = true ]; then
  echo -e "  - ${BOLD}VNC Browser URL:${NC}    ${GREEN}http://localhost:7900${NC} (Manual ChatGPT/Gemini Logins)"
  echo -e "  - ${BOLD}Worker Status:${NC}      ${GREEN}NATIVE HOST (GPU Enabled)${NC}"
else
  echo -e "  - ${BOLD}VNC Browser URL:${NC}    ${GREEN}http://localhost:7900${NC} (Manual ChatGPT/Gemini Logins)"
  echo -e "  - ${BOLD}Worker Status:${NC}      ${GREEN}DOCKER CONTAINER (CPU Mode)${NC}"
fi
echo -e "${BOLD}${CYAN}=====================================================${NC}"
echo ""
echo -e "To view web logs, run:"
echo -e "  ${BOLD}docker compose logs -f app${NC}"
if [[ "$RUN_MODE" == "dashboard" ]]; then
  echo -e "Worker/browser services are stopped in dashboard mode."
elif [ "$USE_NATIVE_WORKER" = true ]; then
  echo -e "To view native worker logs, run:"
  echo -e "  ${BOLD}tail -f logs/native_worker.log${NC}"
else
  echo -e "To view worker logs, run:"
  echo -e "  ${BOLD}docker compose logs -f worker${NC}"
fi
echo ""
echo -e "To stop all services, run:"
echo -e "  ${BOLD}bash run.sh --stop${NC}"
echo -e "${BOLD}${CYAN}=====================================================${NC}"
