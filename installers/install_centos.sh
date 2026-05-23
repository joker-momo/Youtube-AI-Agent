#!/usr/bin/env bash
# CentOS/RedHat/Rocky Linux Installer Script for YouTube AI Agent
# Assumes a fresh machine, installs Docker, Docker Compose, and boots the project.

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
echo -e "${BOLD}${BLUE}   YouTube AI Agent - CentOS/RedHat Setup Wizard     ${NC}"
echo -e "${BOLD}${BLUE}=====================================================${NC}"
echo ""

# Helper function to print step starts
step() {
  echo -e "${BOLD}${CYAN}👉 [Step] $1...${NC}"
}

# Helper function to print success
success() {
  echo -e "${GREEN}✅ $1${NC}\n"
}

# Helper function to print warnings
warn() {
  echo -e "${YELLOW}⚠️  $1${NC}"
}

# Helper function to print errors
error() {
  echo -e "${RED}❌ $1${NC}" >&2
}

# Ensure script is run with bash and check for yum/dnf
if ! command -v yum &>/dev/null; then
  error "This script is designed for CentOS/RHEL/Rocky/Fedora systems using yum/dnf. Exiting."
  exit 1
fi

# 1. Update Package Index & Install Prerequisites
step "Installing system prerequisites"
sudo yum install -y yum-utils device-mapper-persistent-data lvm2 git curl
success "Prerequisites installed successfully!"

# 2. Add Docker Repository
step "Setting up Docker repository"
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
success "Docker repository added!"

# 3. Install Docker Engine, CLI, and Compose
step "Installing Docker Engine & Plugins"
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
success "Docker and Docker Compose installed successfully!"

# 4. Start and Enable Docker Service
step "Starting and enabling Docker daemon"
sudo systemctl start docker
sudo systemctl enable docker
success "Docker daemon is active and running!"

# 5. Configure User Group
step "Adding user to the docker group"
if ! groups $USER | grep &>/dev/null "\bdocker\b"; then
  echo -e "${YELLOW}Adding user '$USER' to 'docker' group to run without 'sudo'...${NC}"
  sudo usermod -aG docker $USER
  success "User added to docker group! Note: Group changes will apply on your next login."
  warn "To run Docker commands without sudo in this current shell, run: newgrp docker"
else
  success "User is already in the docker group."
fi

# 6. Environment configuration (.env)
step "Configuring environment variables"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

if [[ ! -f ".env" ]]; then
  echo -e "${YELLOW}Bootstraping .env from .env.example...${NC}"
  cp .env.example .env
  success ".env file created! Open it later to fill in your API keys."
else
  success ".env file already exists."
fi

# 7. Building Multi-Container Stack
step "Building Docker containers"
echo -e "${BLUE}Running 'docker compose build'... (this compiles TTS models and prepares the browser runtime)${NC}"
if docker info &>/dev/null; then
  docker compose build
else
  sudo docker compose build
fi
success "Docker containers built successfully!"

# 8. Booting the stack
step "Launching YouTube AI Agent"
if docker info &>/dev/null; then
  docker compose up -d app worker browser-worker browser-runtime
else
  sudo docker compose up -d app worker browser-worker browser-runtime
fi
success "All services started successfully in the background!"

echo -e "${BOLD}${GREEN}=====================================================${NC}"
echo -e "${BOLD}${GREEN}      ✨ Setup Completed Successfully! ✨            ${NC}"
echo -e "${BOLD}${GREEN}=====================================================${NC}"
echo ""
echo -e "${BOLD}${YELLOW}⚠️  CRITICAL ACTION REQUIRED ON NEW MACHINE:${NC}"
echo -e "You ${BOLD}must${NC} configure the ${BOLD}.env${NC} file in the repository root before running jobs."
echo -e "Open the ${CYAN}.env${NC} file and fill in your keys:"
echo -e "  - ${BOLD}TELEGRAM_BOT_TOKEN${NC} & ${BOLD}TELEGRAM_CHAT_ID${NC} (For status/progress alerts)"
echo -e "  - ${BOLD}PEXELS_API_KEY${NC} & ${BOLD}PIXABAY_API_KEY${NC} (For stock video/image downloads)"
echo ""
echo -e "You can now access the following endpoints in your browser:"
echo -e "  - ${BOLD}Web Dashboard:${NC}    ${CYAN}http://localhost:8000${NC}"
echo -e "  - ${BOLD}Browser Runtime (VNC):${NC} ${CYAN}http://localhost:7900${NC} (For manual ChatGPT/Claude logins)"
echo ""
echo -e "To view running logs, execute:"
echo -e "  ${BOLD}docker compose logs -f${NC}"
echo ""
echo -e "To stop the agent, run:"
echo -e "  ${BOLD}docker compose down${NC}"
echo ""
echo -e "To start it again anytime, use our runner script:"
echo -e "  ${BOLD}bash installers/run.sh${NC}"
echo -e "${BOLD}${GREEN}=====================================================${NC}"
