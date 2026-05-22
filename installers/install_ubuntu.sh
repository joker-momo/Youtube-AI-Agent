#!/usr/bin/env bash
# Ubuntu/Debian Installer Script for YouTube AI Agent
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
echo -e "${BOLD}${BLUE}   YouTube AI Agent - Ubuntu/Debian Setup Wizard     ${NC}"
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

# Ensure script is run with bash (not sh) and check for apt-get
if ! command -v apt-get &>/dev/null; then
  error "This script is designed for Debian/Ubuntu systems using apt-get. Exiting."
  exit 1
fi

# 1. Update Apt Package Index & Install Prerequisites
step "Updating package list & installing prerequisites"
sudo apt-get update -y
sudo apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    software-properties-common
success "Prerequisites installed successfully!"

# 2. Add Docker's Official GPG Key
step "Adding Docker's GPG key"
sudo mkdir -p /etc/apt/keyrings
if [[ ! -f "/etc/apt/keyrings/docker.gpg" ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
else
  warn "Docker GPG key already exists, skipping download."
fi
success "GPG key registered!"

# 3. Set Up Stable Repository
step "Setting up Docker repository"
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
success "Docker repository added!"

# 4. Install Docker Engine, CLI, and Plugins
step "Installing Docker Engine & Plugins"
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
success "Docker and Docker Compose installed successfully!"

# 5. Start and Enable Docker Service
step "Starting and enabling Docker daemon"
sudo systemctl start docker
sudo systemctl enable docker
success "Docker daemon is active and running!"

# 6. Configure User Group
step "Adding user to the docker group"
if ! groups $USER | grep &>/dev/null "\bdocker\b"; then
  echo -e "${YELLOW}Adding user '$USER' to 'docker' group to run without 'sudo'...${NC}"
  sudo usermod -aG docker $USER
  success "User added to docker group! Note: Group changes will apply on your next login."
  warn "To run Docker commands without sudo in this current shell, run: newgrp docker"
else
  success "User is already in the docker group."
fi

# 7. Environment configuration (.env)
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

# 8. Building Multi-Container Stack
step "Building Docker containers"
echo -e "${BLUE}Running 'docker compose build'... (this compiles TTS models and prepares the browser runtime)${NC}"
# Use sudo here just in case docker group changes haven't loaded in the current shell
if docker info &>/dev/null; then
  docker compose build
else
  sudo docker compose build
fi
success "Docker containers built successfully!"

# 9. Booting the stack
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
