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

# 3. Start Containers
echo -e "${CYAN}Starting YouTube AI Agent services...${NC}"
docker compose up -d app worker browser-worker browser-runtime

# 4. Check Health
echo -e "\n${GREEN}✅ Services started successfully!${NC}"
echo -e "${BOLD}${CYAN}=====================================================${NC}"
echo -e "  - ${BOLD}Dashboard URL:${NC}      ${GREEN}http://localhost:8000${NC}"
echo -e "  - ${BOLD}VNC Browser URL:${NC}    ${GREEN}http://localhost:7900${NC} (Manual ChatGPT/Claude Logins)"
echo -e "${BOLD}${CYAN}=====================================================${NC}"
echo ""
echo -e "To view realtime logs, run:"
echo -e "  ${BOLD}docker compose logs -f${NC}"
echo ""
echo -e "To stop all services, run:"
echo -e "  ${BOLD}docker compose down${NC}"
echo -e "${BOLD}${CYAN}=====================================================${NC}"
