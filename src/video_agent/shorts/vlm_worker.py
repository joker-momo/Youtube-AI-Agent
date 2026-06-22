"""Persistent MLX VLM worker subprocess (spec v4.0.3 §38).

Runs the Qwen-VL judge in its OWN process so the MLX/Metal VLM does not crash
co-resident with the torch-based vision tiers (SigLIP / Grounding DINO) — observed
on 16GB Apple Silicon. The model loads once; requests stream over stdin/stdout as
JSON lines. No media leaves the machine (local file paths only).

Protocol:
  worker → ``{"ready": true}`` (or ``{"ready": false, "error": ...}``) after load
  caller → ``{"image_paths": [...], "question": "...", "max_tokens": 160}`` per line
  worker → ``{"text": "..."}`` or ``{"error": "..."}`` per line
  caller → ``{"cmd": "quit"}`` to stop
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    # Default matches the production config (channel.yaml visual_quality_flow.vlm)
    # so an argv-less invocation never silently downloads a second VLM variant.
    model_id = sys.argv[1] if len(sys.argv) > 1 else "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
    try:
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
        from PIL import Image

        model, processor = load(model_id)
    except Exception as exc:  # noqa: BLE001 - report load failure to the caller
        sys.stdout.write(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        sys.stdout.flush()
        return 1

    sys.stdout.write(json.dumps({"ready": True}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:  # noqa: BLE001
            sys.stdout.write(json.dumps({"error": "bad_json"}) + "\n")
            sys.stdout.flush()
            continue
        if req.get("cmd") == "quit":
            break
        try:
            images = [Image.open(p).convert("RGB") for p in req.get("image_paths") or []]
            prompt = apply_chat_template(processor, model.config, req["question"], num_images=len(images))
            out = generate(model, processor, prompt, images, max_tokens=int(req.get("max_tokens", 160)),
                           verbose=False)
            text = getattr(out, "text", None) or (out if isinstance(out, str) else str(out))
            sys.stdout.write(json.dumps({"text": text}) + "\n")
        except Exception as exc:  # noqa: BLE001
            sys.stdout.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
