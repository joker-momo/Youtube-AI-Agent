#!/usr/bin/env bash
# macOS Installer Script for YouTube AI Agent
# Assumes a fresh machine, installs Homebrew, Docker Desktop, and boots the project.

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
echo -e "${BOLD}${BLUE}      YouTube AI Agent - macOS Setup Wizard          ${NC}"
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

# 1. Check/Install Homebrew
step "Checking for Homebrew"
if ! command -v brew &>/dev/null; then
  echo -e "${YELLOW}Homebrew is not installed. Installing it non-interactively...${NC}"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  
  # Add Homebrew to PATH depending on architecture
  if [[ -f "/opt/homebrew/bin/brew" ]]; then
    # Apple Silicon
    eval "$(/opt/homebrew/bin/brew shellenv)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
  elif [[ -f "/usr/local/bin/brew" ]]; then
    # Intel Mac
    eval "$(/usr/local/bin/brew shellenv)"
    echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
  fi
  success "Homebrew installed successfully!"
else
  success "Homebrew is already installed at: $(which brew)"
fi

# 2. Check/Install Git
step "Checking for Git"
if ! command -v git &>/dev/null; then
  echo -e "${YELLOW}Git is not installed. Installing via Homebrew...${NC}"
  brew install git
  success "Git installed successfully!"
else
  success "Git is already installed at: $(which git)"
fi

# 3. Check/Install Docker Desktop
step "Checking for Docker Desktop"
if ! command -v docker &>/dev/null || ! brew list --cask docker &>/dev/null; then
  if [[ -d "/Applications/Docker.app" ]]; then
    success "Docker Desktop application detected in /Applications."
  else
    echo -e "${YELLOW}Docker Desktop not found. Installing via Homebrew Cask (this may take a few minutes)...${NC}"
    brew install --cask docker
    success "Docker Desktop installed successfully!"
  fi
else
  success "Docker CLI is already installed at: $(which docker)"
fi

# 4. Starting Docker Desktop
step "Starting Docker Desktop application"
if ! docker info &>/dev/null; then
  echo -e "${YELLOW}Docker daemon is not running. Launching Docker Desktop...${NC}"
  open -a Docker
  
  echo -e "${BLUE}Waiting for Docker daemon to become ready (this may take up to 2 minutes)...${NC}"
  echo -e "${YELLOW}Please authorize Docker Desktop with your macOS password if prompted.${NC}"
  
  count=0
  until docker info &>/dev/null; do
    echo -ne "."
    sleep 3
    count=$((count + 3))
    if [[ $count -ge 120 ]]; then
      echo ""
      error "Docker took too long to start. Please open 'Docker' manually from your Applications folder, then run this installer again."
      exit 1
    fi
  done
  echo ""
  success "Docker daemon is active and running!"
else
  success "Docker daemon is already active!"
fi

# 5. Environment configuration (.env)
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

# 6. Building Multi-Container Stack
step "Building Docker containers"
echo -e "${BLUE}Running 'docker compose build'... (this compiles TTS models and prepares the browser runtime)${NC}"
docker compose build
success "Docker containers built successfully!"

# 7. Booting the stack
step "Launching YouTube AI Agent"
docker compose up -d app worker browser-worker browser-runtime
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
