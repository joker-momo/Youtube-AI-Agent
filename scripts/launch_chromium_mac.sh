#!/usr/bin/env bash
# Launch Playwright's bundled Chromium with a persistent profile and CDP
# debug port so the browser-worker (FastAPI) can connect via Playwright's
# connect_over_cdp on http://127.0.0.1:9222.
#
# Uses Playwright's installed Chromium (run `playwright install chromium`
# once). Profile dir is reused across runs so ChatGPT/Gemini logins persist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

PROFILE_DIR="${BROWSER_PROFILE_DIR:-${REPO_DIR}/browser_profiles/default}"
CDP_PORT="${CHROME_CDP_PORT:-9222}"

mkdir -p "${PROFILE_DIR}"

if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi

# Browser selection. Override via BROWSER env var:
#   BROWSER=chrome        — Google Chrome stable
#   BROWSER=canary        — Google Chrome Canary
#   BROWSER=edge          — Microsoft Edge
#   BROWSER=brave         — Brave Browser
#   BROWSER=arc           — Arc
#   BROWSER=playwright    — Playwright bundled Chromium (default)
#   BROWSER=<absolute_path_to_binary>
BROWSER="${BROWSER:-brave}"

case "${BROWSER}" in
  chrome)      CHROMIUM_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ;;
  canary)      CHROMIUM_BIN="/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary" ;;
  edge)        CHROMIUM_BIN="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ;;
  brave)       CHROMIUM_BIN="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" ;;
  arc)         CHROMIUM_BIN="/Applications/Arc.app/Contents/MacOS/Arc" ;;
  playwright)
    CHROMIUM_BIN="$(python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()" 2>/dev/null)"
    ;;
  /*)          CHROMIUM_BIN="${BROWSER}" ;;
  *)           echo "Unknown BROWSER='${BROWSER}'. Use chrome|canary|edge|brave|arc|playwright|<abs_path>" >&2; exit 1 ;;
esac

if [[ -z "${CHROMIUM_BIN}" || ! -x "${CHROMIUM_BIN}" ]]; then
  echo "Browser binary not found at: ${CHROMIUM_BIN}" >&2
  if [[ "${BROWSER}" == "playwright" ]]; then
    echo "Run: playwright install chromium" >&2
  fi
  exit 1
fi

echo "Browser: ${BROWSER} (${CHROMIUM_BIN})"

# Kill any existing Chromium on this debug port to avoid CDP conflicts.
if lsof -i ":${CDP_PORT}" -t >/dev/null 2>&1; then
  echo "Port ${CDP_PORT} already in use; reusing existing Chromium."
  exit 0
fi

echo "Launching Chromium with CDP on 127.0.0.1:${CDP_PORT}"
echo "Profile: ${PROFILE_DIR}"

nohup "${CHROMIUM_BIN}" \
  --remote-debugging-port="${CDP_PORT}" \
  --user-data-dir="${PROFILE_DIR}" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=Translate,MediaRouter \
  > logs/chromium.log 2>&1 &

CHROMIUM_PID=$!
mkdir -p "${REPO_DIR}/.run"
echo "${CHROMIUM_PID}" > "${REPO_DIR}/.run/chromium.pid"
echo "Chromium PID: ${CHROMIUM_PID}"
echo "Log: logs/chromium.log"
