# Browser Appliance

The Browser Appliance is the production pattern this project uses to give
Playwright a real, persistent Chromium that survives container restarts
without depending on the user's host Chrome. The browser is packaged as a
container "appliance" — a service with a fixed endpoint, mounted profile
volume, observable UI, and an internal control plane — and the
business-logic worker connects to it as a client.

## Topology

```text
┌──────────────────────────────────────────────────────────┐
│ docker compose                                           │
│                                                          │
│  ┌──────────────┐         ┌──────────────────────────┐   │
│  │ browser-     │   CDP   │ browser-runtime          │   │
│  │ worker       │ ──────► │                          │   │
│  │              │   WS    │ ┌──────────────────────┐ │   │
│  │ FastAPI      │         │ │ Xvfb :99             │ │   │
│  │ Playwright   │         │ │ fluxbox              │ │   │
│  │ drivers      │ ◄────── │ │ x11vnc :5900         │ │   │
│  └──────────────┘  status │ │ noVNC  :7900         │ │   │
│        ▲                  │ │ socat  :9222 →:9223  │ │   │
│        │ HTTP             │ │ Chromium headed      │ │   │
│  ┌─────┴────────┐         │ └──────────────────────┘ │   │
│  │ app          │         │      ▲ /data/profile     │   │
│  │ FastAPI      │         └──────┼───────────────────┘   │
│  │ orchestrator │                │ volume               │
│  └──────────────┘                ▼                       │
│                              ./browser_profiles/default  │
└──────────────────────────────────────────────────────────┘
   ▲                                ▲
   │ 127.0.0.1:8000 (orchestrator)  │ 127.0.0.1:7900 (noVNC UI)
   │ 127.0.0.1:8001 (worker)        │
```

Networks:

- `appliance_net` is an internal bridge network shared by `app`,
  `browser-worker`, and `browser-runtime`. The runtime's CDP port (9222)
  is reachable on this network only — never published to the host or LAN.
- noVNC is bound to `127.0.0.1:7900` so the operator can watch the
  headed browser and complete manual logins.

## Services

### `browser-runtime`

- Image: `mcr.microsoft.com/playwright:v1.49.0-jammy` + `xvfb`,
  `fluxbox`, `x11vnc`, `novnc`, `websockify`, `socat`, `supervisor`.
- `supervisord` runs five long-lived programs:
  1. `Xvfb :99` — virtual framebuffer for the headed Chromium.
  2. `fluxbox` — minimal window manager so the page renders inside a
     window the noVNC viewer can show.
  3. `x11vnc` — VNC bridge to display `:99`.
  4. `novnc / websockify` — HTTP/WebSocket wrapper around x11vnc on
     port `7900`.
  5. `socat` — forwards `0.0.0.0:9222 → 127.0.0.1:9223` because
     Chromium 119+ refuses to bind CDP outside loopback.
  6. `chromium` — `launch-chromium.sh` runs the bundled Chromium from
     the Playwright image with `--user-data-dir=/data/profile` and
     `--remote-debugging-port=9223`.
- Volume `./browser_profiles/default:/data/profile` keeps cookies,
  localStorage, IndexedDB, extension state, and autofill across restarts.
- Container is stateful in the volume-only sense; the container itself
  can be recreated freely.

### `browser-worker`

- Same image as `app` (`video-agent` Python stack + Playwright).
- FastAPI service that connects to the runtime as a Playwright client
  and exposes:
  - `GET /health` — process liveness.
  - `GET /runtime` — connects to the runtime and reports
    `{contexts, pages}` (HTTP 503 with the underlying error when the
    runtime is not reachable).
  - `GET /auth/{site}/status` — opens a page for ChatGPT or Gemini and
    reports whether the operator needs to log in via noVNC.
  - `POST /chatgpt/send` — body `{prompt, response_timeout_ms?}`. Opens
    a fresh temporary chat at `chatgpt.com/?model=gpt-4o&temporary-chat=true`,
    types the prompt, sends, waits for the assistant turn to settle, and
    returns `{site, raw_response}`. Returns HTTP 409 with
    `login_required: true` when the profile is signed out, HTTP 502 with
    a screenshot path when a selector or response fails.
  - `POST /gemini/send` — same contract for `gemini.google.com/app`.
- Stateless; restart freely.

## CDP host rewrite

Chromium's `/json/version` advertises a `webSocketDebuggerUrl` like
`ws://127.0.0.1:9223/devtools/browser/<id>`. Playwright would follow
that literally and fail because `127.0.0.1` inside the worker container
is the worker itself. The worker calls
`_resolve_browser_ws(base_cdp_url)` which:

1. Fetches `${CHROME_CDP_URL}/json/version` with `Host: localhost` (CDP
   rejects any other Host header to defend against DNS rebinding).
2. Reads the advertised websocket URL.
3. Rewrites its `netloc` to match the original `CHROME_CDP_URL` (i.e.
   `browser-runtime:9222`).
4. Hands the rewritten URL to `playwright.chromium.connect_over_cdp(...)`.

## First-time bootstrap

1. `docker compose up -d browser-runtime browser-worker`.
2. Wait ~10 seconds for Chromium to launch.
3. `curl http://127.0.0.1:8001/runtime` should return `{ok: true, ...}`.
4. Open `http://127.0.0.1:7900/vnc.html` (noVNC) and click *Connect*.
   You'll see the live Chromium window.
5. Inside that Chromium, navigate to ChatGPT, Gemini, vidIQ, etc. and
   log in once. Cookies persist into `./browser_profiles/default/`.
6. `curl http://127.0.0.1:8001/auth/chatgpt/status` should report
   `logged_in: true` after login.

## Stale `SingletonLock` cleanup

If the container is killed hard, the profile keeps a `SingletonLock`,
`SingletonCookie`, and `SingletonSocket` from the old process and a
fresh Chromium refuses to start. `launch-chromium.sh` removes those
files before launch, so a normal compose restart recovers automatically.

## Scaling per-channel

Each channel/account should have its own runtime + worker pair so logins
don't collide:

```yaml
services:
  browser-runtime-codex:
    extends: { service: browser-runtime }
    volumes: ["./browser_profiles/codex:/data/profile"]
    ports:   ["127.0.0.1:7900:7900"]
  browser-worker-codex:
    extends: { service: browser-worker }
    environment:
      CHROME_CDP_URL: http://browser-runtime-codex:9222

  browser-runtime-channel2:
    extends: { service: browser-runtime }
    volumes: ["./browser_profiles/channel2:/data/profile"]
    ports:   ["127.0.0.1:7901:7900"]
  browser-worker-channel2:
    extends: { service: browser-worker }
    environment:
      CHROME_CDP_URL: http://browser-runtime-channel2:9222
```

## Backup

Profiles are plain directories; back them up with `tar`:

```bash
tar czf backup-default-$(date +%F).tgz browser_profiles/default/
```

## Security notes

- CDP port 9222 is **never** published to the host. Only the internal
  Docker network `appliance_net` can reach it.
- noVNC is bound to `127.0.0.1`. If you need to reach it from another
  machine, tunnel over SSH (`ssh -L 7900:127.0.0.1:7900 host`).
- Chromium runs with `--no-sandbox` because Docker's default seccomp
  profile strips the required capability. Run the runtime container
  only on trusted workstations.
- For multi-user setups, set a VNC password via `x11vnc -passwd ...`
  and front noVNC with Caddy/nginx + basic auth + TLS.
