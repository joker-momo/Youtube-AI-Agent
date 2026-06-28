#!/usr/bin/env bash
# macOS Native Installer for YouTube AI Agent.
# No Docker. Installs Homebrew, Python 3.11, Node 22, ffmpeg, project deps,
# Playwright Chromium, and Remotion. Optimized for Apple Silicon
# (VideoToolbox HW encode + MPS PyTorch + Metal/ANGLE Chromium).

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

step() { echo -e "${BOLD}${CYAN}👉 $1${NC}"; }
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; exit 1; }

echo -e "${BOLD}${BLUE}=====================================================${NC}"
echo -e "${BOLD}${BLUE}  YouTube AI Agent — macOS Native Setup (no Docker)  ${NC}"
echo -e "${BOLD}${BLUE}=====================================================${NC}"

[[ "$(uname -s)" == "Darwin" ]] || fail "This installer is macOS only."

# 1. Homebrew
step "Checking Homebrew"
if ! command -v brew &>/dev/null; then
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -f "/opt/homebrew/bin/brew" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
  elif [[ -f "/usr/local/bin/brew" ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
    echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
  fi
fi
ok "Homebrew: $(brew --version | head -n1)"

# 2. System packages
step "Installing system packages (python@3.11, node@22, ffmpeg, espeak-ng, git)"
brew install python@3.11 node@22 ffmpeg espeak-ng git >/dev/null
brew link --overwrite --force node@22 >/dev/null 2>&1 || true

PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
[[ -x "${PYTHON_BIN}" ]] || fail "python@3.11 missing after brew install"
ok "Python: $(${PYTHON_BIN} --version)"
ok "Node: $(node --version)   npm: $(npm --version)"
ok "ffmpeg: $(ffmpeg -version | head -n1)"

# 3. Python venv + deps
step "Creating .venv and installing Python deps"
if [[ ! -d ".venv" ]]; then
  "${PYTHON_BIN}" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel
python -m pip install -r requirements.txt
python -m pip install -e .
ok "Python deps installed"

# 4. Playwright Chromium
step "Installing Playwright Chromium"
python -m playwright install chromium
ok "Playwright Chromium ready"

# 5. Remotion
step "Installing Remotion deps"
npm --prefix remotion ci
npx --prefix remotion remotion browser ensure
ok "Remotion ready"

# 6. Warmup local model caches (best effort)
step "Warming up local TTS/ASR caches (best effort)"
mkdir -p caches/huggingface caches/whisper logs
python -c "import whisper; whisper.load_model('tiny'); print('Whisper tiny: ok')" || warn "Whisper warmup skipped"
python -c "from kokoro import KPipeline; KPipeline(lang_code='e', repo_id='hexgrad/Kokoro-82M'); print('Kokoro: ok')" || warn "Kokoro warmup skipped"

# 6b. MeloTTS sidecar venv (Elena voice — provider: melo). Isolated from .venv
#     because MeloTTS' old deps would clash with the project venv. Best-effort.
step "Building MeloTTS sidecar venv (Elena voice)"
PYTHON311="${PYTHON_BIN}" bash tools/setup-melo-venv.sh \
  || warn "MeloTTS venv setup skipped — run 'bash tools/setup-melo-venv.sh' later (narration is SILENT without it)"

# 7. .env scaffold
if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
  ok ".env created from .env.example"
fi

echo ""
echo -e "${BOLD}${GREEN}Install complete.${NC}"
echo -e "Start everything:  ${BOLD}bash run.sh${NC}"
echo -e "Stop everything:   ${BOLD}bash run.sh --stop${NC}"
