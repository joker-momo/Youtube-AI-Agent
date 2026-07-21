#!/usr/bin/env bash
# Launch a Chromium-family browser with a persistent profile and a CDP debug
# port so the browser-worker (FastAPI) can attach via connect_over_cdp on
# http://127.0.0.1:9222.
#
# macOS reliability (bridge 20260709): a .app bundle must be launched with
# `open -na "<App>" --args …`, NOT `nohup "<inner binary>" …`. The inner Mach-O
# re-execs and detaches, so nohup loses it and the process exits immediately with
# an empty log and no listener. We also DO NOT `source .venv/bin/activate` — a
# relocated checkout leaves a stale VIRTUAL_ENV that breaks the Python lookup;
# we call .venv/bin/python directly instead. After launching, we POLL the CDP
# port so the script only succeeds once the listener is actually up (deterministic
# lifecycle check), and fails loudly otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_DIR}"

PROFILE_DIR="${BROWSER_PROFILE_DIR:-${REPO_DIR}/browser_profiles/default}"
CDP_PORT="${CHROME_CDP_PORT:-9222}"
READY_TIMEOUT_SEC="${CDP_READY_TIMEOUT_SEC:-20}"

mkdir -p "${PROFILE_DIR}" logs "${REPO_DIR}/.run"

# Repo-local Python WITHOUT activating (a relocated venv has a stale VIRTUAL_ENV
# that silently points the wrong interpreter — bridge 20260709 root cause).
VENV_PY="${REPO_DIR}/.venv/bin/python"
[[ -x "${VENV_PY}" ]] || VENV_PY="python3"

# Browser selection. Override via BROWSER env var:
#   chrome | canary | edge | brave | arc | playwright | <absolute_path_to_binary>
BROWSER="${BROWSER:-brave}"

# For .app bundles we launch via `open -na <APP_BUNDLE>`; for a raw binary
# (playwright's bundled Chromium, or an absolute path) we background it directly.
APP_BUNDLE=""
CHROMIUM_BIN=""
case "${BROWSER}" in
  chrome)   APP_BUNDLE="/Applications/Google Chrome.app" ;;
  canary)   APP_BUNDLE="/Applications/Google Chrome Canary.app" ;;
  edge)     APP_BUNDLE="/Applications/Microsoft Edge.app" ;;
  brave)    APP_BUNDLE="/Applications/Brave Browser.app" ;;
  arc)      APP_BUNDLE="/Applications/Arc.app" ;;
  playwright)
    CHROMIUM_BIN="$("${VENV_PY}" -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()" 2>/dev/null || true)"
    ;;
  /*)       CHROMIUM_BIN="${BROWSER}" ;;
  *)        echo "Unknown BROWSER='${BROWSER}'. Use chrome|canary|edge|brave|arc|playwright|<abs_path>" >&2; exit 1 ;;
esac

# Reuse an already-listening CDP browser instead of racing a second instance.
if lsof -nP -iTCP:"${CDP_PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "CDP port ${CDP_PORT} already listening; reusing existing browser."
  exit 0
fi

CDP_ARGS=(
  --remote-debugging-port="${CDP_PORT}"
  --user-data-dir="${PROFILE_DIR}"
  --no-first-run
  --no-default-browser-check
  --disable-features=Translate,MediaRouter
)

if [[ -n "${APP_BUNDLE}" ]]; then
  if [[ ! -d "${APP_BUNDLE}" ]]; then
    echo "App bundle not found: ${APP_BUNDLE}" >&2
    exit 1
  fi
  echo "Launching ${BROWSER} (${APP_BUNDLE}) with CDP on 127.0.0.1:${CDP_PORT}"
  echo "Profile: ${PROFILE_DIR}"
  # -n: new instance; -a: this app; --args: pass the CDP flags through. This is
  # the macOS-correct launch that survives (unlike nohup on the inner binary).
  open -na "${APP_BUNDLE}" --args "${CDP_ARGS[@]}" > logs/chromium.log 2>&1
else
  if [[ -z "${CHROMIUM_BIN}" || ! -x "${CHROMIUM_BIN}" ]]; then
    echo "Browser binary not found at: ${CHROMIUM_BIN:-<empty>}" >&2
    [[ "${BROWSER}" == "playwright" ]] && echo "Run: ${VENV_PY} -m playwright install chromium" >&2
    exit 1
  fi
  echo "Launching ${BROWSER} (${CHROMIUM_BIN}) with CDP on 127.0.0.1:${CDP_PORT}"
  echo "Profile: ${PROFILE_DIR}"
  nohup "${CHROMIUM_BIN}" "${CDP_ARGS[@]}" > logs/chromium.log 2>&1 &
  echo "$!" > "${REPO_DIR}/.run/chromium.pid"
fi

# Deterministic lifecycle check: succeed ONLY when the CDP listener is actually
# up. connect_over_cdp cannot repair a launch that never bound the port.
echo "Waiting up to ${READY_TIMEOUT_SEC}s for CDP :${CDP_PORT} to listen…"
for _ in $(seq 1 "${READY_TIMEOUT_SEC}"); do
  if lsof -nP -iTCP:"${CDP_PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
    # Record the actual listening PID (open -na does not give us the child PID).
    lsof -nP -iTCP:"${CDP_PORT}" -sTCP:LISTEN -t 2>/dev/null | head -1 > "${REPO_DIR}/.run/chromium.pid" || true
    echo "CDP :${CDP_PORT} is listening. PID: $(cat "${REPO_DIR}/.run/chromium.pid" 2>/dev/null || echo '?')"
    echo "Log: logs/chromium.log"
    exit 0
  fi
  sleep 1
done

echo "ERROR: CDP :${CDP_PORT} never came up within ${READY_TIMEOUT_SEC}s." >&2
echo "Check logs/chromium.log and that ${BROWSER} is not already running with a" >&2
echo "conflicting profile. See scripts/cdp_smoke.py for an attach health probe." >&2
exit 1
