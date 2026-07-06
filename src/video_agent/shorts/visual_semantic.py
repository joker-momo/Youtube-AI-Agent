"""Optional local semantic vision adapters (spec v4.0.3 §12A, §13, §38).

Quality-first three-tier cascade, all **local**, feature-flagged, default OFF:

    SigLIP-2        topic pre-filter  — is the clip broadly on-topic?
    Qwen2.5-VL-7B   holistic judge    — right subject(45+) / action / scene / brand?
    Grounding DINO  forbidden grounding — is a forbidden object actually present?

Design rules (non-negotiable):
- Baseline pipeline correctness NEVER depends on these. A missing library or model
  weight yields ``CAPABILITY_UNAVAILABLE`` evidence — never a PASS for a critical
  semantic requirement (§12A.3).
- Forbidden critical evidence is grounded by the detector (Grounding DINO), not the
  VLM alone (§38: "critical forbidden evidence should not rely on CLIP alone";
  VLMs hallucinate).
- 100% local: no media is sent to any remote service.
- Models are loaded lazily and cached; on Apple Silicon SigLIP/DINO use MPS and the
  VLM uses MLX (native), per project priority #4.

Evidence statuses (§12A.2): CONFIRMED_PRESENT, CONFIRMED_ABSENT, SUPPORTED,
CONTRADICTED, UNKNOWN, CAPABILITY_UNAVAILABLE.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

# HuggingFace tokenizers (Grounding DINO / SigLIP processors) deadlock on their
# rayon thread-pool when the worker process has forked while TOKENIZERS_PARALLELISM
# is unset: encode_batch blocks forever on a semaphore (~0 CPU, no progress). The
# VLM subprocess already sets this; the main worker (which runs SigLIP + DINO
# in-process) did not. Disable parallel tokenization process-wide before any
# transformers/tokenizers import. setdefault so an explicit override still wins.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL.Image import Image

# Default model ids — quality-first picks, overridable via config.
DEFAULT_SIGLIP_MODEL = "google/siglip2-base-patch16-224"
DEFAULT_VLM_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
DEFAULT_DINO_MODEL = "IDEA-Research/grounding-dino-base"

# Generic off-topic prompts the intended subject must out-rank (relative scoring).
# Cover the real failure modes seen on short-05: dog, feet-POV, urban/cars, child.
SIGLIP_DISTRACTORS = [
    "a dog",
    "a close-up of feet or shoes",
    "an empty city street with cars",
    "a young child",
    "plain text on a solid background",
    "an indoor office or store",
]


@dataclass(frozen=True)
class SemanticConfig:
    """Resolved semantic-adapter configuration (from shorts.visual_quality_flow.local_qa)."""

    enabled: bool = False
    use_siglip: bool = True
    use_vlm: bool = True
    use_detector: bool = True
    siglip_model: str = DEFAULT_SIGLIP_MODEL
    vlm_model: str = DEFAULT_VLM_MODEL
    detector_model: str = DEFAULT_DINO_MODEL
    device: str = "auto"  # auto | mps | cpu (VLM always MLX on Apple Silicon)
    siglip_reject_margin: float = 1.0  # logit margin a distractor must beat intent by → CONTRADICTED
    detector_box_threshold: float = 0.35  # forbidden object present above this
    max_frames: int = 4  # frames sampled per clip for semantic passes
    # Demographic age gate (SigLIP zero-shot) — restores 45+ audience-fit gating
    # without the OOM-prone VLM. When on, a clip whose people read as young adults
    # (young out-ranks 45+ by this logit margin) is CONTRADICTED on age_band.
    age_gate: bool = False
    age_reject_margin: float = 0.5


def resolve_semantic_config(local_qa_cfg: dict[str, Any]) -> SemanticConfig:
    """Build a :class:`SemanticConfig` from the local_qa config block.

    ``semantic_adapter: none`` (default) → disabled. ``clip`` enables only SigLIP;
    ``clip_vlm`` enables SigLIP+VLM; ``full`` enables all three. ``detector_adapter``
    independently forces the detector on.
    """
    adapter = str(local_qa_cfg.get("semantic_adapter") or "none").strip().lower()
    detector_flag = str(local_qa_cfg.get("detector_adapter") or "none").strip().lower()
    if adapter == "none" and detector_flag == "none":
        return SemanticConfig(enabled=False)
    models = local_qa_cfg.get("semantic_models") or {}
    thresholds = local_qa_cfg.get("semantic_thresholds") or {}
    return SemanticConfig(
        enabled=True,
        use_siglip=adapter in {"clip", "clip_vlm", "full"},
        use_vlm=adapter in {"clip_vlm", "full"},
        use_detector=adapter == "full" or detector_flag not in {"none", ""},
        siglip_model=str(models.get("siglip") or DEFAULT_SIGLIP_MODEL),
        vlm_model=str(models.get("vlm") or DEFAULT_VLM_MODEL),
        detector_model=str(models.get("detector") or DEFAULT_DINO_MODEL),
        device=str(local_qa_cfg.get("device") or "auto"),
        siglip_reject_margin=float(
            thresholds.get("siglip_reject_margin", thresholds.get("siglip_reject", 1.0))
        ),
        detector_box_threshold=float(thresholds.get("detector_box", 0.35)),
        max_frames=int(local_qa_cfg.get("semantic_max_frames", 4)),
        age_gate=bool(local_qa_cfg.get("enforce_age_band_45_plus", False)),
        age_reject_margin=float(thresholds.get("siglip_age_reject", 0.5)),
    )


# --------------------------------------------------------------------------- #
# Frame sampling (local ffmpeg)
# --------------------------------------------------------------------------- #
def extract_frames(video_path: Path, timestamps_sec: list[float]) -> list[Image]:
    """Extract frames at the given timestamps as PIL images (local ffmpeg)."""
    from PIL import Image

    images: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as td:
        for i, ts in enumerate(timestamps_sec):
            out = Path(td) / f"f{i:02d}.jpg"
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, ts):.3f}",
                 "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(out)],
                capture_output=True,
            )
            if proc.returncode == 0 and out.exists():
                images.append(Image.open(out).convert("RGB").copy())
    return images


def default_timestamps(duration_sec: float, max_frames: int) -> list[float]:
    """Sample timestamps across the selected trim. With >=3 frames we cover trim
    START / MIDPOINT / END so a required action is confirmed to PERSIST across the
    clip rather than appear in a single lucky frame (a 1-frame sample lets cooking
    footage pass for "sit down / breathe / write"). The first frame still matters
    most (§8/§23), so any extra frames bias toward the first second."""
    if duration_sec <= 0:
        return [0.0]
    if max_frames <= 1:
        return [0.0]
    end = round(max(0.0, duration_sec - 0.05), 3)
    if max_frames == 2:
        return sorted({0.0, end})
    pts = {0.0, round(duration_sec / 2.0, 3), end}
    for k in range(max_frames - 3):  # extra frames: first-second bias
        pts.add(round(min(0.4 + 0.3 * k, end), 3))
    return sorted(pts)[:max_frames]


def _evidence(
    requirement: str, status: str, *, source: str, model: str, model_version: str | None,
    asset_id: str | None, confidence: float | None, reason: str,
) -> dict[str, Any]:
    return {
        "requirement": requirement, "status": status, "capability_source": source,
        "model": model, "model_version": model_version, "asset_id": asset_id,
        "confidence": confidence, "reason": reason,
    }


# --------------------------------------------------------------------------- #
# Adapter protocol + lazy model singletons
# --------------------------------------------------------------------------- #
class FrameSemanticAdapter(Protocol):
    name: str

    def available(self) -> bool: ...

    def evaluate(
        self, images: list[Image], *, required_tags: dict[str, list[str]],
        forbidden_tags: dict[str, list[str]], visual_intent: str, asset_id: str | None,
    ) -> list[dict[str, Any]]: ...


def _resolve_torch_device(pref: str) -> str:
    try:
        import torch

        if pref == "cpu":
            return "cpu"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        return "cpu"
    return "cpu"


# Process-level cache of loaded (model, processor) pairs, keyed by
# (tag, model_id, device). build_semantic_analyzer builds fresh adapter instances
# per Short, and each adapter previously cold-loaded its weights + hit Hugging
# Face metadata endpoints (HEAD/GET, 404s for optional files) every time. Caching
# here loads each model once per worker process across all Shorts. Never holds a
# failed load, so a transient failure can still recover on a later attempt.
_HF_MODEL_CACHE: dict[tuple[str, str, str], tuple[Any, Any]] = {}


def _load_hf_pair(
    tag: str, model_cls: Any, proc_cls: Any, model_id: str, device: str
) -> tuple[Any, Any] | None:
    """Return a cached ``(model, processor)`` for ``model_id`` on ``device``,
    loading it at most once per process. Tries the local snapshot first
    (``local_files_only=True``) so an already-downloaded model skips all Hugging
    Face metadata round-trips, then falls back to a normal online load. Returns
    ``None`` (and caches nothing) when the model cannot be loaded at all, so the
    adapter degrades to CAPABILITY_UNAVAILABLE exactly as before."""
    key = (tag, model_id, device)
    cached = _HF_MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    for local_only in (True, False):
        try:
            proc = proc_cls.from_pretrained(model_id, local_files_only=local_only)
            model = model_cls.from_pretrained(model_id, local_files_only=local_only).to(device).eval()
        except Exception:  # noqa: BLE001 - missing lib/weights/network → try online, then give up
            continue
        _HF_MODEL_CACHE[key] = (model, proc)
        return _HF_MODEL_CACHE[key]
    return None


class SigLipTopicAdapter:
    """Cheap topic pre-filter via SigLIP-2 (transformers, MPS). Records SUPPORTED /
    CONTRADICTED / UNKNOWN for the span's overall visual intent + required subjects."""

    name = "siglip"

    def __init__(self, cfg: SemanticConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._proc = None
        self._device = "cpu"
        self._loaded = False
        self._broken = False

    def _load(self) -> bool:
        if self._loaded:
            return self._model is not None
        self._loaded = True
        try:
            from transformers import AutoModel, AutoProcessor

            self._device = _resolve_torch_device(self.cfg.device)
            pair = _load_hf_pair(
                "siglip", AutoModel, AutoProcessor, self.cfg.siglip_model, self._device
            )
            if pair is None:
                self._model = None
                self._broken = True
            else:
                self._model, self._proc = pair
        except Exception:  # noqa: BLE001 - missing lib/weights → unavailable
            self._model = None
            self._broken = True
        return self._model is not None

    def available(self) -> bool:
        return self._load()

    def evaluate(self, images, *, required_tags, forbidden_tags, visual_intent, asset_id):
        if not images or not self._load():
            return [_evidence("topic:visual_intent", "CAPABILITY_UNAVAILABLE",
                              source="optional_semantic_model", model=self.cfg.siglip_model,
                              model_version=None, asset_id=asset_id, confidence=None,
                              reason="siglip unavailable (lib/weights missing)")]
        import torch

        # SigLIP absolute sigmoid scores are tiny and topic-uncalibrated; the robust
        # signal is RELATIVE — does the intended subject out-rank generic off-topic
        # distractors? (Validated on real footage: dog/feet clips rank a distractor
        # highest, walker clips rank the intent highest.)
        targets = [visual_intent or "the described scene"] + [
            t.replace("_", " ") for t in (required_tags.get("required_subject_tags") or [])
        ]
        prompts = targets + SIGLIP_DISTRACTORS
        with torch.no_grad():
            inputs = self._proc(text=prompts, images=images, return_tensors="pt",
                                padding="max_length", truncation=True).to(self._device)
            # logits_per_image: [n_images, n_prompts] → mean over frames
            scores = self._model(**inputs).logits_per_image.float().mean(dim=0).tolist()
        n_targets = len(targets)
        distractor_best = max(scores[n_targets:]) if len(scores) > n_targets else float("-inf")
        records: list[dict[str, Any]] = []
        labels = ["topic:visual_intent"] + [
            f"required_subject:{t}" for t in (required_tags.get("required_subject_tags") or [])
        ]
        for idx, label in enumerate(labels):
            target = scores[idx]
            margin = target - distractor_best
            if margin >= 0:
                status = "SUPPORTED"
            elif margin <= -self.cfg.siglip_reject_margin:
                status = "CONTRADICTED"
            else:
                status = "UNKNOWN"
            records.append(_evidence(label, status, source="optional_semantic_model",
                                     model=self.cfg.siglip_model, model_version=self.cfg.siglip_model,
                                     asset_id=asset_id, confidence=round(float(margin), 3),
                                     reason=f"siglip intent-vs-distractor logit margin={margin:.2f}"))

        # Demographic age gate (45+ audience fit). Zero-shot SigLIP: if the people
        # in the clip read as young adults more than as 45+, CONTRADICT age_band so
        # the span is rejected (→ lazy on-brand AI elderly image). Object/no-person
        # clips score both prompts similarly → small margin → not rejected.
        already_checks_age = any(
            "age_band_45_plus" in t for t in (required_tags.get("required_subject_tags") or [])
        )
        if self.cfg.age_gate and not already_checks_age:
            age_prompts = [
                "an older adult aged 45 or older, a mature or senior person",
                "a young adult in their twenties or thirties",
            ]
            with torch.no_grad():
                a_inputs = self._proc(text=age_prompts, images=images, return_tensors="pt",
                                      padding="max_length", truncation=True).to(self._device)
                a_scores = self._model(**a_inputs).logits_per_image.float().mean(dim=0).tolist()
            older_score, young_score = a_scores[0], a_scores[1]
            age_margin = young_score - older_score  # > 0 → reads younger than 45+
            age_status = "CONTRADICTED" if age_margin >= self.cfg.age_reject_margin else "SUPPORTED"
            records.append(_evidence(
                "required_subject:age_band_45_plus", age_status, source="optional_semantic_model",
                model=self.cfg.siglip_model, model_version=self.cfg.siglip_model, asset_id=asset_id,
                confidence=round(float(-age_margin), 3),
                reason=f"siglip age gate young-vs-45+ margin={age_margin:.2f}"))
        return records


class _VlmWorkerClient:
    """Manages a persistent MLX VLM worker subprocess (loads the model once, in its
    own process). Isolation is required because the MLX/Metal VLM crashes when
    co-resident with the torch vision tiers on 16GB Apple Silicon."""

    def __init__(self, model_id: str, *, load_timeout: float = 180.0, judge_timeout: float = 120.0) -> None:
        self.model_id = model_id
        self.load_timeout = load_timeout
        self.judge_timeout = judge_timeout
        self._proc: subprocess.Popen | None = None
        self._ready = False
        self._broken = False

    def _readline(self, timeout: float) -> str | None:
        """Read one line from the worker, or None on timeout (so a hung/OOM-killed
        worker degrades to CAPABILITY_UNAVAILABLE instead of hanging the build)."""
        import select

        if self._proc is None or self._proc.stdout is None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            return None
        return self._proc.stdout.readline()

    def _kill(self) -> None:
        self._broken = True
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def ensure(self) -> bool:
        if self._proc is not None:
            return self._ready
        if self._broken:
            return False
        import sys

        worker = str(Path(__file__).resolve().parent / "vlm_worker.py")
        # OBJC_DISABLE_INITIALIZE_FORK_SAFETY: spawning a child after torch/Metal is
        # initialized triggers the macOS Objective-C fork-safety abort; this disables
        # it for the (immediately exec'd) worker. TOKENIZERS/HF: quiet + offline-ish.
        env = {**os.environ, "TOKENIZERS_PARALLELISM": "false", "HF_HUB_DISABLE_TELEMETRY": "1",
               "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES"}
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - fixed local worker script
                [sys.executable, worker, self.model_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env, bufsize=1,
            )
            line = self._readline(self.load_timeout)  # bounded wait for model load
            self._ready = bool(json.loads(line or "{}").get("ready"))
        except Exception:  # noqa: BLE001
            self._ready = False
        if not self._ready:
            self._kill()
        return self._ready

    def judge(self, image_paths: list[str], question: str, *, max_tokens: int = 160) -> str | None:
        if not self.ensure() or self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            return None
        try:
            self._proc.stdin.write(
                json.dumps({"image_paths": image_paths, "question": question, "max_tokens": max_tokens}) + "\n"
            )
            self._proc.stdin.flush()
            line = self._readline(self.judge_timeout)
            if line is None:  # worker hung → degrade, don't block the build
                self._kill()
                return None
            return json.loads(line or "{}").get("text")
        except Exception:  # noqa: BLE001
            self._broken = True
            return None

    def close(self) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self._proc.kill()


class QwenVlJudgeAdapter:
    """Holistic judge via Qwen2.5-VL on MLX, run in an ISOLATED subprocess worker
    (the MLX VLM crashes co-resident with the torch vision tiers on 16GB). Judges
    subject demographic, action, environment, and brand fit from sampled frames."""

    name = "vlm"

    def __init__(self, cfg: SemanticConfig) -> None:
        self.cfg = cfg
        self._worker = _VlmWorkerClient(cfg.vlm_model)

    def available(self) -> bool:
        return self._worker.ensure()

    def evaluate(self, images, *, required_tags, forbidden_tags, visual_intent, asset_id):
        def _unavailable(reason: str) -> list[dict[str, Any]]:
            return [_evidence("scene:brand_intent", "CAPABILITY_UNAVAILABLE",
                              source="optional_semantic_model", model=self.cfg.vlm_model,
                              model_version=None, asset_id=asset_id, confidence=None, reason=reason)]

        if not images:
            return _unavailable("no frames to judge")
        req = ", ".join(
            t.replace("_", " ") for k in ("required_subject_tags", "required_action_tags",
                                          "required_environment_tags") for t in (required_tags.get(k) or [])
        )
        question = (
            f"This footage is for a calm health/wellness Short for adults 45+. Intended scene: "
            f"'{visual_intent}'. Required elements: {req or 'mature adult, calm wellness context'}. "
            "Answer strictly as JSON {subject_ok:bool, action_ok:bool, environment_ok:bool, "
            "brand_fit:bool, reason:str} — subject_ok means a clearly mature adult (45+) is the focus."
        )
        with tempfile.TemporaryDirectory() as td:
            paths_list: list[str] = []
            for i, im in enumerate(images):
                p = Path(td) / f"f{i}.jpg"
                im.save(p)
                paths_list.append(str(p))
            text = self._worker.judge(paths_list, question)
        if text is None:
            return _unavailable("qwen-vl worker unavailable (mlx/weights missing or crashed)")
        verdict = _parse_vlm_json(text)
        records: list[dict[str, Any]] = []
        for key, requirement in (("subject_ok", "required_subject:age_band_45_plus"),
                                 ("action_ok", "required_action:intended_action"),
                                 ("environment_ok", "required_environment:intended_environment"),
                                 ("brand_fit", "scene:brand_intent")):
            val = verdict.get(key)
            status = "SUPPORTED" if val is True else "CONTRADICTED" if val is False else "UNKNOWN"
            records.append(_evidence(requirement, status, source="optional_semantic_model",
                                     model=self.cfg.vlm_model, model_version=self.cfg.vlm_model,
                                     asset_id=asset_id, confidence=None,
                                     reason=str(verdict.get("reason") or "vlm judgment")))
        return records


def _parse_vlm_json(text: str) -> dict[str, Any]:
    import json
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return {}


class GroundingDinoForbiddenAdapter:
    """Authoritative forbidden-object grounding via Grounding DINO (transformers,
    MPS). Only escalates to CONFIRMED_PRESENT (→ CONTRADICTED for a forbidden tag)
    when an object is actually detected above threshold — never hallucinated."""

    name = "detector"

    def __init__(self, cfg: SemanticConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._proc = None
        self._device = "cpu"
        self._loaded = False

    def _load(self) -> bool:
        if self._loaded:
            return self._model is not None
        self._loaded = True
        try:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

            # Grounding DINO uses ops (torch.cummax) not implemented on Metal/MPS,
            # so default to CPU (base model is fast enough one-shot). Honor an
            # explicit non-auto device if the operator forces it.
            self._device = "cpu" if self.cfg.device in ("auto", "cpu") else _resolve_torch_device(self.cfg.device)
            pair = _load_hf_pair(
                "detector", AutoModelForZeroShotObjectDetection, AutoProcessor,
                self.cfg.detector_model, self._device,
            )
            if pair is None:
                self._model = None
            else:
                self._model, self._proc = pair
        except Exception:  # noqa: BLE001
            self._model = None
        return self._model is not None

    def available(self) -> bool:
        return self._load()

    def evaluate(self, images, *, required_tags, forbidden_tags, visual_intent, asset_id):
        forbidden = [
            t.replace("_", " ")
            for k in ("forbidden_subject_tags", "forbidden_evidence_tags", "forbidden_action_tags")
            for t in (forbidden_tags.get(k) or [])
        ]
        if not forbidden:
            return []
        if not images or not self._load():
            return [_evidence(f"forbidden_evidence:{t}", "CAPABILITY_UNAVAILABLE",
                              source="optional_semantic_model", model=self.cfg.detector_model,
                              model_version=None, asset_id=asset_id, confidence=None,
                              reason="grounding-dino unavailable") for t in forbidden]
        import torch

        text = ". ".join(forbidden) + "."
        present: dict[str, float] = {}
        with torch.no_grad():
            for img in images:
                inputs = self._proc(images=img, text=text, return_tensors="pt").to(self._device)
                outputs = self._model(**inputs)
                results = self._proc.post_process_grounded_object_detection(
                    outputs, inputs.input_ids, threshold=self.cfg.detector_box_threshold,
                    text_threshold=0.25, target_sizes=[img.size[::-1]],
                )[0]
                # transformers renamed the matched-phrase key to ``text_labels``.
                labels = results.get("text_labels") or results.get("labels") or []
                for label, score in zip(labels, results.get("scores", []), strict=False):
                    s = float(score)
                    key = str(label).strip().lower()
                    if s > present.get(key, 0.0):
                        present[key] = s
        records: list[dict[str, Any]] = []
        for tag in forbidden:
            score = max((v for k, v in present.items() if tag in k or k in tag), default=0.0)
            if score >= self.cfg.detector_box_threshold:
                records.append(_evidence(f"forbidden_evidence:{tag}", "CONFIRMED_PRESENT",
                                         source="deterministic_local", model=self.cfg.detector_model,
                                         model_version=self.cfg.detector_model, asset_id=asset_id,
                                         confidence=round(score, 4),
                                         reason=f"grounding-dino detected forbidden '{tag}' (score={score:.2f})"))
            else:
                records.append(_evidence(f"forbidden_evidence:{tag}", "UNKNOWN",
                                         source="optional_semantic_model", model=self.cfg.detector_model,
                                         model_version=self.cfg.detector_model, asset_id=asset_id,
                                         confidence=None,
                                         reason="forbidden object not detected (absence is not confirmation)"))
        return records


# --------------------------------------------------------------------------- #
# Cascade
# --------------------------------------------------------------------------- #
@dataclass
class CascadeSemanticAnalyzer:
    cfg: SemanticConfig
    adapters: list[FrameSemanticAdapter] = field(default_factory=list)

    def capability_summary(self) -> dict[str, bool]:
        return {a.name: bool(a.available()) for a in self.adapters}

    def analyze_span(
        self, *, video_path: str | Path | None, duration_sec: float,
        required_tags: dict[str, list[str]], forbidden_tags: dict[str, list[str]],
        visual_intent: str, asset_id: str | None,
    ) -> list[dict[str, Any]]:
        """Run the enabled cascade over sampled frames; return §12A evidence records.

        The cheap SigLIP tier runs first; if it CONTRADICTS the topic, the expensive
        VLM is skipped (cost gate, §14/§38) but the detector still runs for forbidden
        grounding. Any adapter that cannot load contributes CAPABILITY_UNAVAILABLE.
        """
        if not video_path or not Path(video_path).exists():
            return [_evidence("topic:visual_intent", "CAPABILITY_UNAVAILABLE",
                              source="unknown", model="none", model_version=None,
                              asset_id=asset_id, confidence=None,
                              reason="no local finalist file to analyze")]
        images = extract_frames(Path(video_path), default_timestamps(duration_sec, self.cfg.max_frames))
        kw = dict(required_tags=required_tags, forbidden_tags=forbidden_tags,
                  visual_intent=visual_intent, asset_id=asset_id)
        records: list[dict[str, Any]] = []
        topic_contradicted = False
        for adapter in self.adapters:
            if adapter.name == "vlm" and topic_contradicted:
                continue  # cheap gate already rejected the topic; skip expensive judge
            recs = adapter.evaluate(images, **kw)
            records.extend(recs)
            if adapter.name == "siglip":
                topic_contradicted = any(
                    r["requirement"] == "topic:visual_intent" and r["status"] == "CONTRADICTED"
                    for r in recs
                )
        return records


def build_semantic_analyzer(local_qa_cfg: dict[str, Any]) -> CascadeSemanticAnalyzer | None:
    """Factory: returns a configured cascade, or ``None`` when semantic adapters are
    off (``semantic_adapter: none``) so the baseline path is completely unaffected."""
    cfg = resolve_semantic_config(local_qa_cfg or {})
    if not cfg.enabled:
        return None
    adapters: list[FrameSemanticAdapter] = []
    if cfg.use_siglip:
        adapters.append(SigLipTopicAdapter(cfg))
    if cfg.use_vlm:
        adapters.append(QwenVlJudgeAdapter(cfg))
    if cfg.use_detector:
        adapters.append(GroundingDinoForbiddenAdapter(cfg))
    return CascadeSemanticAnalyzer(cfg=cfg, adapters=adapters)
