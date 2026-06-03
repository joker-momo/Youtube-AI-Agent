#!/usr/bin/env bash
# Master installer — macOS native only (no Docker).

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLERS_DIR="${SCRIPT_DIR}/installers"

[[ -d "${INSTALLERS_DIR}" ]] || { echo -e "${RED}installers/ missing${NC}"; exit 1; }
chmod +x "${INSTALLERS_DIR}"/*.sh

if [[ "$OSTYPE" != "darwin"* ]]; then
  echo -e "${RED}This project now supports macOS only (Apple Silicon recommended).${NC}"
  echo -e "Docker / Linux / Windows installers have been removed."
  exit 1
fi

echo -e "${BOLD}${BLUE}macOS native install${NC}"
exec bash "${INSTALLERS_DIR}/install_macos.sh"
