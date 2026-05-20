#!/usr/bin/env bash
# Launch the Chromium bundled with the Playwright image in headed mode
# against the X virtual framebuffer started by supervisord. The browser
# persists its profile in /data/profile (mounted volume) so manual logins
# survive container restarts. CDP listens on 9222 inside the container only;
# the docker-compose internal network exposes it to the browser-worker but
# never publishes the port to the host.
set -euo pipefail

PROFILE_DIR="${CHROME_PROFILE_DIR:-/data/profile}"
# Chromium forces CDP bind to loopback for safety; socat re-publishes
# CHROME_CDP_INTERNAL_PORT (loopback) on 0.0.0.0:9222 inside the
# container so the worker can reach it over the docker network.
CDP_PORT="${CHROME_CDP_INTERNAL_PORT:-9223}"
WINDOW_SIZE="${CHROME_WINDOW_SIZE:-1366,768}"

mkdir -p "$PROFILE_DIR"

# Clear stale Singleton* files left by an unclean previous run.
# Without this, Chromium refuses to start because it thinks another
# process owns the profile.
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonCookie" "$PROFILE_DIR/SingletonSocket"

CHROME_BIN="$(ls -d /ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | sort | tail -n1)"
if [[ -z "$CHROME_BIN" || ! -x "$CHROME_BIN" ]]; then
  echo "Chromium binary not found under /ms-playwright" >&2
  exit 1
fi

echo "Launching $CHROME_BIN"
echo "Profile: $PROFILE_DIR"
echo "CDP: 0.0.0.0:$CDP_PORT (internal network only)"

exec "$CHROME_BIN" \
  --user-data-dir="$PROFILE_DIR" \
  --remote-debugging-port="$CDP_PORT" \
  --remote-allow-origins=* \
  --window-size="$WINDOW_SIZE" \
  --window-position=0,0 \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --disable-gpu \
  --no-sandbox
