"""JSON extraction and prompt JSON utility helpers for operator workflows."""
from __future__ import annotations

import json
from typing import Any

_JSON_CTRL_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}


def _escape_control_chars_in_strings(text: str) -> str:
    """Escape raw control characters that appear INSIDE JSON string literals.

    Models often emit literal newlines/tabs inside string values (invalid JSON),
    while structural whitespace between tokens must stay untouched. A blanket
    ``str.replace`` corrupts structural newlines; this walks the text tracking
    string state and only escapes control chars while inside a string.
    """
    out: list[str] = []
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                out.append(char)
                escape = False
                continue
            if char == "\\":
                out.append(char)
                escape = True
                continue
            if char == '"':
                out.append(char)
                in_string = False
                continue
            code = ord(char)
            if code < 0x20:
                out.append(_JSON_CTRL_ESCAPES.get(code, f"\\u{code:04x}"))
            else:
                out.append(char)
        else:
            if char == '"':
                in_string = True
            out.append(char)
    return "".join(out)


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Extract all parseable JSON objects found in ``text``.

    Useful when the model returns commentary plus multiple JSON blocks.
    """
    objects: list[dict[str, Any]] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start == -1:
            break

        depth = 0
        in_string = False
        escape = False
        parsed_successfully = False

        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0:
                        chunk = text[start : idx + 1]
                        try:
                            parsed = json.loads(chunk)
                            if isinstance(parsed, dict):
                                objects.append(parsed)
                                index = idx + 1
                                parsed_successfully = True
                                break
                        except Exception:
                            # ChatGPT sometimes emits literal control characters
                            # (newlines/tabs) inside JSON string values (invalid
                            # per JSON spec). Escape only those inside strings —
                            # a blanket replace would corrupt structural
                            # whitespace between tokens.
                            try:
                                repaired = _escape_control_chars_in_strings(chunk)
                                parsed = json.loads(repaired)
                                if isinstance(parsed, dict):
                                    objects.append(parsed)
                                    index = idx + 1
                                    parsed_successfully = True
                                    # Log so anomalies show up in operator
                                    # output without needing a debugger.
                                    print(
                                        "[operator] extract_json_objects: repaired raw newlines in model output",
                                        flush=True,
                                    )
                                    break
                            except Exception:
                                pass

        if not parsed_successfully:
            index = start + 1

    # Fallback: recover a truncated outermost object. Models occasionally cut
    # off the stream before the final closing brace(s) (the browser-worker
    # stability detector can latch a hair early). The inner objects above parse
    # fine, but the intended root never closes, so it is missing here. Balance
    # the open braces/brackets from the first '{' and retry. This is a no-op for
    # already-complete output (nothing is left unclosed).
    first = text.find("{")
    if first != -1:
        repaired_root = _repair_truncated_json_object(text[first:])
        if repaired_root is not None and repaired_root not in objects:
            objects.insert(0, repaired_root)

    return objects


def is_json_object_complete(text: str) -> bool:
    """Return whether the first JSON object has all structural closers.

    ``extract_json_objects`` deliberately repairs truncated model output for
    best-effort promotion.  Callers that can request a continuation need the
    pre-repair truth so they do not mistake a synthetically closed object for a
    complete response.
    """
    start = text.find("{")
    if start == -1:
        return False

    stack: list[str] = []
    in_string = False
    escape = False
    for char in text[start:]:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char == "}":
            if not stack or stack[-1] != "{":
                return False
            stack.pop()
            if not stack:
                return True
        elif char == "]":
            if not stack or stack[-1] != "[":
                return False
            stack.pop()

    return False


def _repair_truncated_json_object(span: str) -> dict[str, Any] | None:
    """Best-effort repair of a JSON object truncated mid-stream.

    Tracks string state, builds a stack of unclosed ``{``/``[``, and appends the
    matching closers in reverse order. Returns the parsed dict, or ``None`` when
    nothing was unclosed (input already complete) or the repair still fails.
    """
    stack: list[str] = []
    in_string = False
    escape = False
    for char in span:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif char == "]":
            if stack and stack[-1] == "[":
                stack.pop()
    if not stack:
        return None  # already balanced — not truncated

    closers = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    repaired = span.rstrip()
    if in_string:
        repaired += '"'
    if repaired.endswith(","):
        repaired = repaired[:-1]
    repaired += closers

    for attempt in (
        repaired,
        _escape_control_chars_in_strings(repaired),
    ):
        try:
            parsed = json.loads(attempt)
        except Exception:
            continue
        if isinstance(parsed, dict):
            print(
                "[operator] extract_json_objects: recovered truncated root object",
                flush=True,
            )
            return parsed
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    candidates = extract_json_objects(text)
    if not candidates:
        raise ValueError("No JSON object found in model output.")
    return candidates[0]


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _json_file_directive(filename: str) -> str:
    """Output rules that force the model to return one complete, named JSON file.

    Long JSON responses get truncated when streamed inline. We ask the model to
    deliver exactly one fenced ```json block whose first line names the file
    (``// FILE: <name>``) so the response is self-identifying and easy to manage,
    and we forbid splitting/truncation so the JSON is always complete in a single
    turn. The ``// FILE:`` marker sits before the first ``{`` so JSON extraction
    (which scans from ``{``) ignores it.
    """
    return "\n".join(
        [
            "⚠️ OUTPUT RULES — READ CAREFULLY:",
            "• Return EXACTLY ONE fenced ```json code block and NOTHING else "
            "(no text before or after the block).",
            f"• The FIRST line inside the block must be exactly: // FILE: {filename}",
            "• On the lines after it, output the COMPLETE JSON object.",
            "• The JSON must be COMPLETE and VALID in this single response.",
            "• NEVER truncate, NEVER split across multiple messages, NEVER write "
            "\"continue\" or \"...\".",
            "• If the content is long, still close every brace and bracket — "
            "completeness is mandatory.",
        ]
    )
