#!/usr/bin/env bash
# Launch host Chrome with remote debugging on port 9222 using a dedicated profile.
# The browser-worker container attaches via CDP and reuses your manual logins
# (ChatGPT Plus, Gemini, vidIQ). The system never auto-logs-in; you sign in once
# in this profile, then keep it running while jobs execute.
set -euo pipefail

PORT="${CHROME_CDP_PORT:-9222}"
PROFILE_DIR="${CHROME_CDP_PROFILE:-$HOME/.video-agent/chrome-cdp-profile}"
PROFILE_DIRECTORY="${CHROME_PROFILE_DIRECTORY:-}"

mkdir -p "$PROFILE_DIR"

detect_chrome() {
  if [[ -n "${CHROME_BIN:-}" ]]; then
    echo "$CHROME_BIN"
    return
  fi
  case "$(uname -s)" in
    Darwin)
      echo "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      ;;
    Linux)
      command -v google-chrome || command -v google-chrome-stable || command -v chromium
      ;;
    *)
      echo "Unsupported platform. Set CHROME_BIN." >&2
      exit 1
      ;;
  esac
}

CHROME_BIN="$(detect_chrome)"

if [[ ! -x "$CHROME_BIN" ]]; then
  echo "Chrome binary not executable: $CHROME_BIN" >&2
  exit 1
fi

echo "Launching Chrome with CDP on port $PORT"
echo "Profile: $PROFILE_DIR"
if [[ -n "$PROFILE_DIRECTORY" ]]; then
  echo "Chrome profile directory: $PROFILE_DIRECTORY"
fi

PROFILE_DIRECTORY_ARGS=()
if [[ -n "$PROFILE_DIRECTORY" ]]; then
  PROFILE_DIRECTORY_ARGS=(--profile-directory="$PROFILE_DIRECTORY")
fi

exec "$CHROME_BIN" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  "${PROFILE_DIRECTORY_ARGS[@]}" \
  --no-first-run \
  --no-default-browser-check
