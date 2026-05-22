#!/usr/bin/env bash
# Master Installer Script for YouTube AI Agent
# Automatically detects the operating system and invokes the correct installer script.

set -euo pipefail

# Text formatting helper constants
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${BOLD}${BLUE}=====================================================${NC}"
echo -e "${BOLD}${BLUE}       YouTube AI Agent - Master Installer           ${NC}"
echo -e "${BOLD}${BLUE}=====================================================${NC}"
echo -e "Detecting operating system..."

# Check if installers directory exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLERS_DIR="${SCRIPT_DIR}/installers"

if [[ ! -d "${INSTALLERS_DIR}" ]]; then
  echo -e "${RED}❌ Error: installers/ directory not found in the current folder.${NC}"
  echo -e "Please ensure you are running this script from the root of the cloned repository."
  exit 1
fi

# Make scripts executable
chmod +x "${INSTALLERS_DIR}"/*.sh

# Detect OS
OS_NAME="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
  OS_NAME="macos"
elif [[ -f "/etc/os-release" ]]; then
  # Read os-release file
  . /etc/os-release
  if [[ "${ID}" == "ubuntu" || "${ID_LIKE:-}" == *"ubuntu"* || "${ID}" == "debian" || "${ID_LIKE:-}" == *"debian"* ]]; then
    OS_NAME="ubuntu"
  elif [[ "${ID}" == "centos" || "${ID}" == "rhel" || "${ID}" == "rocky" || "${ID}" == "almalinux" ]]; then
    OS_NAME="centos"
  fi
fi

case "${OS_NAME}" in
  "macos")
    echo -e "${GREEN}Detected macOS! Starting macOS installation...${NC}\n"
    exec bash "${INSTALLERS_DIR}/install_macos.sh"
    ;;
  "ubuntu")
    echo -e "${GREEN}Detected Ubuntu/Debian Linux! Starting installation...${NC}\n"
    exec bash "${INSTALLERS_DIR}/install_ubuntu.sh"
    ;;
  "centos")
    echo -e "${GREEN}Detected CentOS/RHEL/Rocky Linux! Starting installation...${NC}\n"
    exec bash "${INSTALLERS_DIR}/install_centos.sh"
    ;;
  *)
    echo -e "${RED}❌ Could not automatically detect a supported operating system.${NC}"
    echo -e "Supported operating systems:"
    echo -e "  - macOS"
    echo -e "  - Ubuntu / Debian"
    echo -e "  - CentOS / RHEL / Rocky Linux"
    echo ""
    echo -e "If you are on WSL (Windows Subsystem for Linux), please run the Ubuntu installer manually:"
    echo -e "  ${BOLD}bash installers/install_ubuntu.sh${NC}"
    echo ""
    exit 1
    ;;
esac
