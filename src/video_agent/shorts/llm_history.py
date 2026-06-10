"""Per-Short LLM call history recorder.

Captures every ChatGPT and Gemini prompt+response made while building a single
Short — including failed QA attempts and every regeneration retry — into one
append-only JSONL file: ``shorts/<short_id>/json/llm_history.jsonl``.

Design: the recorder wraps the injected ``llm_fn`` / ``gemini_fn`` at the
``build_short`` chokepoint, so all sub-builders (script, scenes, seo, thumbnail)
and QA passes are logged without touching their call sites. The wrapper accepts
both call shapes used in the codebase: ``fn(prompt)`` and ``fn(kind, prompt)``.

Each JSONL line is one call:
    {
      "seq": 1,
      "ts": "2026-06-07T...Z",
      "provider": "chatgpt" | "gemini",
      "kind": "script" | "scenes" | "seo" | "qa_script" | "qa_scenes" | "?",
      "attempt": 2,
      "prompt_chars": 1234,
      "prompt": "...full prompt...",
      "response_chars": 567,
      "response": "...full response... (or null on error)",
      "duration_ms": 8421,
      "ok": true,
      "error": null
    }
"""
from __future__ import annotations

import datetime
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _guess_kind(provider: str, prompt: str) -> str:
    """Best-effort stage label from prompt content (kind is not always passed)."""
    p = prompt or ""
    pl = p.lower()
    if provider == "gemini":
        if "scene" in pl:
            return "qa_scenes"
        if "script" in pl or "guion" in pl or "guión" in pl:
            return "qa_script"
        return "qa"
    # chatgpt
    if "RETRY FEEDBACK" in p and "scene" in pl:
        return "scenes"
    if "seo" in pl or "title" in pl and "description" in pl:
        return "seo"
    if "scene" in pl:
        return "scenes"
    if "script" in pl or "narration" in pl:
        return "script"
    return "?"


class LLMHistoryRecorder:
    """Append-only recorder for one Short's LLM calls."""

    def __init__(self, history_path: Path) -> None:
        self.path = Path(history_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Seed the sequence from any existing lines so a second recorder bound to
        # the same file (e.g. the image-gen recorder created in the assets stage)
        # continues numbering instead of restarting at 1 and colliding.
        self._seq = self._count_existing_lines()
        self._lock = threading.Lock()
        # One-shot kind override for the next wrapped call. Lets a caller that
        # owns the recorder (e.g. the SEO builder's retry loop) tag the upcoming
        # LLM entry as ``seo:attempt-2`` instead of the generic ``seo`` guess, so
        # legitimate self-correction retries are not mistaken for duplicate runs.
        self._kind_hint: str | None = None

    def _count_existing_lines(self) -> int:
        try:
            if not self.path.exists():
                return 0
            with self.path.open("r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:  # pragma: no cover - defensive
            return 0

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def record_event(self, provider: str, kind: str, payload: dict[str, Any], *, ok: bool = True) -> None:
        """Append a non-LLM pipeline event to the same history stream."""
        with self._lock:
            self._seq += 1
            seq = self._seq
        self._append({
            "seq": seq,
            "ts": _now_iso(),
            "provider": provider,
            "kind": kind,
            "prompt_chars": 0,
            "prompt": "",
            "response_chars": 0,
            "response": "",
            "duration_ms": 0,
            "ok": bool(ok),
            "error": None,
            "payload": payload,
        })

    def set_kind_hint(self, kind: str | None) -> None:
        """Tag the NEXT wrapped LLM call with ``kind`` (consumed once)."""
        with self._lock:
            self._kind_hint = kind

    def _consume_kind_hint(self) -> str | None:
        with self._lock:
            hint = self._kind_hint
            self._kind_hint = None
            return hint

    def record_image_gen(
        self,
        prompt: str,
        *,
        provider: str = "chatgpt",
        kind: str = "image_gen",
        ok: bool = True,
        error: str | None = None,
        duration_ms: int = 0,
        response: str = "",
    ) -> None:
        """Append an image-generation call to the same history stream.

        The browser image-gen path does not flow through ``wrap`` (its callable
        has shape ``fn(prompt, out_path) -> None``), so this records the full
        prompt sent to ChatGPT for the AI image fallback explicitly.
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        prompt_str = prompt or ""
        resp_str = response or ""
        self._append({
            "seq": seq,
            "ts": _now_iso(),
            "provider": provider,
            "kind": kind,
            "prompt_chars": len(prompt_str),
            "prompt": prompt_str,
            "response_chars": len(resp_str),
            "response": resp_str if ok else None,
            "duration_ms": int(duration_ms),
            "ok": bool(ok),
            "error": error,
        })

    def wrap(
        self,
        fn: Callable[..., str],
        provider: str,
        *,
        default_kind: str | None = None,
    ) -> Callable[..., str]:
        """Wrap an llm callable so every call is logged. Preserves call shape.

        Accepts ``fn(prompt)`` and ``fn(kind, prompt)``. On error the failure is
        logged (ok=false, error=str) and then re-raised so existing retry logic
        is unchanged.
        """

        def wrapped(*args: Any) -> str:
            if len(args) == 2:
                kind, prompt = str(args[0]), args[1]
            elif len(args) == 1:
                kind, prompt = default_kind, args[0]
            else:  # pragma: no cover - unexpected arity, forward verbatim
                return fn(*args)

            prompt_str = "" if prompt is None else str(prompt)
            label = self._consume_kind_hint() or kind or _guess_kind(provider, prompt_str)
            with self._lock:
                self._seq += 1
                seq = self._seq
            started = _now_iso()
            t0 = time.monotonic()
            try:
                response = fn(*args)
            except Exception as exc:  # log failure then re-raise
                self._append({
                    "seq": seq,
                    "ts": started,
                    "provider": provider,
                    "kind": label,
                    "prompt_chars": len(prompt_str),
                    "prompt": prompt_str,
                    "response_chars": 0,
                    "response": None,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                raise
            resp_str = "" if response is None else str(response)
            self._append({
                "seq": seq,
                "ts": started,
                "provider": provider,
                "kind": label,
                "prompt_chars": len(prompt_str),
                "prompt": prompt_str,
                "response_chars": len(resp_str),
                "response": resp_str,
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "ok": True,
                "error": None,
            })
            return response

        return wrapped


def _verdict_from_text(text: str) -> str:
    """Best-effort PASS/WARN/FAIL extraction from a JSON-ish response string.

    QA and anti-AI prompts flow through ``wrap``; their verdict lives in the
    response body, not a payload, so the transcript would otherwise show a bare
    OK. Find the first ``"verdict": "..."`` occurrence and normalize it.
    """
    if not text:
        return ""
    m = re.search(r'"verdict"\s*:\s*"([^"]+)"', text)
    if not m:
        return ""
    v = m.group(1).strip().upper()
    return v if v in {"PASS", "WARN", "FAIL"} else ""


def _fence(text: str) -> str:
    """Wrap text in a fenced block, escaping nested fences."""
    body = (text or "").replace("```", "ʼʼʼ")
    return f"```\n{body}\n```"


def render_markdown(history: list[dict[str, Any]], *, short_id: str = "") -> str:
    """Human-readable Markdown transcript of one Short's LLM calls."""
    lines: list[str] = []
    title = f"LLM Prompt History — {short_id}" if short_id else "LLM Prompt History"
    lines.append(f"# {title}\n")

    total = len(history)
    fails = sum(1 for h in history if not h.get("ok", True))
    by_provider: dict[str, int] = {}
    for h in history:
        by_provider[h.get("provider", "?")] = by_provider.get(h.get("provider", "?"), 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_provider.items())) or "none"
    lines.append(f"**Total calls:** {total}  ·  **Failed:** {fails}  ·  **By provider:** {summary}\n")
    lines.append("---\n")

    for h in history:
        seq = h.get("seq", "?")
        provider = h.get("provider", "?")
        kind = h.get("kind", "?")
        ok = h.get("ok", True)
        payload = h.get("payload") if isinstance(h.get("payload"), dict) else {}
        verdict = str(payload.get("verdict") or "").upper()
        if not verdict:
            verdict = _verdict_from_text(str(h.get("response") or ""))
        if verdict == "PASS":
            status = "✅ PASS"
        elif verdict == "WARN":
            status = "⚠️ WARN"
        elif verdict == "FAIL":
            status = "❌ FAIL"
        else:
            status = "✅ OK" if ok else "❌ FAIL"
        ms = h.get("duration_ms")
        dur = f"{ms} ms" if isinstance(ms, int) else "?"
        ts = h.get("ts", "")
        lines.append(f"## #{seq} · {provider} · {kind} · {status}")
        lines.append(f"_{ts} · {dur}_\n")
        if not ok and h.get("error"):
            lines.append(f"> **Error:** {h['error']}\n")
        if payload:
            reason = payload.get("error") or payload.get("reason") or payload.get("detail")
            if not reason and isinstance(payload.get("issue"), dict):
                reason = payload["issue"].get("detail")
            if reason:
                lines.append(f"**Reason:** {reason}\n")
            lines.append("**Payload:**\n")
            lines.append(_fence(json.dumps(payload, ensure_ascii=False, indent=2)))
            lines.append("")
        lines.append("**Prompt:**\n")
        lines.append(_fence(str(h.get("prompt") or "")))
        lines.append("")
        if ok:
            lines.append("**Response:**\n")
            lines.append(_fence(str(h.get("response") or "")))
            lines.append("")
        lines.append("---\n")

    return "\n".join(lines)


def read_history(history_path: Path) -> list[dict[str, Any]]:
    """Read all recorded calls (ordered). Skips malformed lines defensively."""
    p = Path(history_path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
