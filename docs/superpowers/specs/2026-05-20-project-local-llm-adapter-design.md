# Project Local LLM Adapter Design

Date: 2026-05-20
Status: Proposed

## Goal

Add a small internal LLM adapter for `Youtube-AI-Agent` so the web app and orchestrator can call ChatGPT and Gemini through the existing `browser-worker` without being tied directly to browser-worker HTTP routes.

This is not a HiveMind clone, not a generic OpenAI-compatible API product, and not a multi-account scaling tool. It exists only to make this project cleaner and easier to swap between browser UI, mock, and future API providers.

## Non-Goals

- Do not bypass ChatGPT, Gemini, or browser rate limits.
- Do not auto-login, inspect cookies, read passwords, or access browser storage.
- Do not build multi-account swarm routing.
- Do not expose a public API server for external tools.
- Do not replace the current browser-worker drivers in this step.
- Do not add InferStack or OpenAI API clients yet.

## Architecture

Introduce a narrow adapter layer in `src/video_agent/orchestrator/`:

- `LLMClient`: protocol for sending chat-style `messages` and receiving raw text.
- `BrowserWorkerLLMClient`: implementation backed by the existing `BrowserClient`.
- `FakeLLMClient`: test helper for deterministic unit tests.

The web app and auto stages should depend on the protocol, not on browser-worker-specific route details. Browser-worker remains responsible for Playwright, ChatGPT, Gemini, session handling, trace screenshots, and login-required errors.

```text
web app / stages
  -> LLMClient protocol
      -> BrowserWorkerLLMClient
          -> BrowserClient
              -> browser-worker HTTP routes
                  -> ChatGPTDriver / GeminiDriver
```

## Data Flow

For one-shot calls:

1. Stage builds a list of messages or a final prompt.
2. `LLMClient.send(site, messages, timeout_ms)` converts messages into the existing prompt shape.
3. `BrowserWorkerLLMClient` calls `BrowserClient.chatgpt_send` or `BrowserClient.gemini_send`.
4. The adapter returns raw response text to the stage.
5. Existing validators and promote logic parse the text into `script.json`, `scenes.json`, `seo.json`, or QA JSON.

For persistent sessions:

1. Web app opens one ChatGPT and one Gemini session through the adapter.
2. The adapter returns a sender callable plus close callable.
3. Existing briefing and sequential stage flow stays intact.
4. Close is always called in `finally`.

## Error Handling

The adapter should preserve browser-worker errors with enough detail for the web UI:

- login required remains a recoverable state with screenshot path if available
- browser-worker HTTP failures become adapter exceptions
- empty model responses remain validation failures in the stage layer
- session close failures are best-effort and should not hide the original stage error

No retry policy is added in the adapter. Retry behavior remains owned by the orchestrator/web flow so the UI can show the exact failed stage.

## Configuration

Use the existing `BROWSER_WORKER_URL` environment variable through `BrowserClient`.

No new global config is required. Future provider selection can be added later as a separate decision.

## Testing

Add focused tests without launching Chrome:

- adapter maps `chatgpt` messages to `BrowserClient.chatgpt_send`
- adapter maps `gemini` messages to `BrowserClient.gemini_send`
- adapter persistent sessions wrap `open_persistent_session`
- web app dependency can be overridden with a fake client
- existing auto-stage tests remain green

Full browser smoke tests remain manual/integration tests because they require logged-in browser profiles.

## Acceptance Criteria

- Auto stages call an `LLMClient` abstraction instead of direct browser-worker send functions.
- Existing v3 web auto flow behavior is unchanged.
- Existing `browser-worker` routes remain backward compatible.
- Unit tests pass without network, Chrome, ChatGPT, or Gemini.
- Docker test suite remains green.

