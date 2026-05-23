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

# 1. Check Docker Daemon
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
else
  echo -e "${BLUE}ℹ️  No compatible Linux GPU detected. Running in standard CPU mode.${NC}"
fi

# 4. Handle macOS Native Worker Speedup Option
USE_NATIVE_WORKER=false
if [[ "$OS" == "Darwin" ]]; then
  echo -e "\n${BOLD}${YELLOW}================ macOS Performance Optimization =================${NC}"
  echo -e "Docker on macOS runs inside a Linux VM and cannot use your Mac's hardware GPU/VideoToolbox."
  echo -e "Running the Worker natively on your Mac provides a ${BOLD}3x+ render speedup${NC}."
  echo -e "Would you like to run the Webapp/Browser inside Docker but run the Worker natively?"
  echo -ne "👉 Run Worker natively on host Mac? (y/N): "
  
  # Read user input with 5 second timeout defaulting to No (n) for automated runs
  if read -t 5 -r REPLY; then
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      USE_NATIVE_WORKER=true
    fi
  else
    REPLY="n"
  fi
  echo -e "${BOLD}${YELLOW}=================================================================${NC}\n"
fi

if [ "$USE_NATIVE_WORKER" = true ]; then
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

  # If .venv exists, verify its python version is >= 3.10
  if [[ -d ".venv" ]]; then
    IS_COMPATIBLE=$(.venv/bin/python -c "import sys; print(sys.version_info >= (3, 10))" 2>/dev/null || echo "False")
    if [[ "$IS_COMPATIBLE" == "False" ]]; then
      echo -e "${YELLOW}Existing .venv is using an incompatible Python version. Recreating...${NC}"
      rm -rf .venv
    fi
  fi

  echo -e "${CYAN}Setting up native Python virtual environment on host Mac...${NC}"
  if [[ ! -d ".venv" ]]; then
    $PYTHON_CMD -m venv .venv
  fi
  source .venv/bin/activate
  echo -e "${BLUE}Upgrading pip...${NC}"
  python -m pip install --quiet --timeout 100 --retries 10 --upgrade pip
  echo -e "${BLUE}Installing Python dependencies (this might take a minute)...${NC}"
  python -m pip install --quiet --timeout 100 --retries 10 -r requirements.txt
  
  # Start app and browser containers in Docker, but exclude the docker worker
  echo -e "${CYAN}Starting App & Browser containers in Docker...${NC}"
  docker compose up -d app browser-worker browser-runtime
  
  # Ensure the docker worker is stopped to prevent conflicts
  docker compose stop worker &>/dev/null || true
  
  # Run the native worker in the background
  echo -e "${CYAN}Launching Native Worker on host Mac (3x speedup)...${NC}"
  mkdir -p logs
  nohup env PYTHONPATH=src python -m video_agent.cli worker --db-path jobs/queue.db > logs/native_worker.log 2>&1 &
  echo -e "${GREEN}✅ Native Worker started in the background (PID: $!). Logs: logs/native_worker.log${NC}"
else
  # Standard Docker startup
  echo -e "${CYAN}Starting YouTube AI Agent services in Docker...${NC}"
  docker compose ${COMPOSE_ARGS} up -d app worker browser-worker browser-runtime
fi

# 5. Check Health
echo -e "\n${GREEN}✅ Services started successfully!${NC}"
echo -e "${BOLD}${CYAN}=====================================================${NC}"
echo -e "  - ${BOLD}Dashboard URL:${NC}      ${GREEN}http://localhost:8000${NC}"
echo -e "  - ${BOLD}VNC Browser URL:${NC}    ${GREEN}http://localhost:7900${NC} (Manual ChatGPT/Claude Logins)"
if [ "$USE_NATIVE_WORKER" = true ]; then
  echo -e "  - ${BOLD}Worker Status:${NC}      ${GREEN}NATIVE HOST (GPU Enabled)${NC}"
else
  echo -e "  - ${BOLD}Worker Status:${NC}      ${GREEN}DOCKER CONTAINER (CPU Mode)${NC}"
fi
echo -e "${BOLD}${CYAN}=====================================================${NC}"
echo ""
echo -e "To view web logs, run:"
echo -e "  ${BOLD}docker compose logs -f app${NC}"
if [ "$USE_NATIVE_WORKER" = true ]; then
  echo -e "To view native worker logs, run:"
  echo -e "  ${BOLD}tail -f logs/native_worker.log${NC}"
else
  echo -e "To view worker logs, run:"
  echo -e "  ${BOLD}docker compose logs -f worker${NC}"
fi
echo ""
echo -e "To stop all services, run:"
if [ "$USE_NATIVE_WORKER" = true ]; then
  echo -e "  ${BOLD}docker compose down && pkill -f \"video_agent.cli worker\"${NC}"
else
  echo -e "  ${BOLD}docker compose down${NC}"
fi
echo -e "${BOLD}${CYAN}=====================================================${NC}"
