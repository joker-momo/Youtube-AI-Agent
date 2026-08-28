"""Pure long-form thumbnail QA — image decode, dHash similarity, injected-OCR
exact-copy checking, cheap visual heuristics, and deterministic selection.

Deliberately pure: no provider calls (OCR results are injected as
`ThumbnailOcrResult`, never fetched here) and no filesystem history discovery
(the orchestrator stage owns that and passes in prior signatures/dHashes).
Runtime dependencies are stdlib + Pillow only.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from PIL import Image

SCHEMA_VERSION = "thumbnail-qa-v1"

EXPECTED_WIDTH = 1920
EXPECTED_HEIGHT = 1080

# §4.3 similarity thresholds — Hamming distance on an 8x8 dHash (0-64).
SIBLING_DHASH_MAX = 6
HISTORY_DHASH_MAX = 5
# §4.4 concept-signature field-difference thresholds.
SIBLING_SIGNATURE_MIN_DIFFERENCES = 5
HISTORY_SIGNATURE_MIN_DIFFERENCES = 3

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARN = "warning"
STATUS_NOT_RUN = "not_run"
STATUS_NOT_AVAILABLE = "not_available"

# §4.5 OCR box-area boundaries (fraction of frame area).
_OCR_AREA_WARN = 0.35
_OCR_AREA_FAIL = 0.40

# §4.6 visual heuristic thresholds.
_CONTRAST_WARN_STD = 12.0
_EDGE_DENSITY_WARN = 0.35
_EDGE_DELTA_THRESHOLD = 30

# Ranking weights for §4.7 selection: package (title/thumbnail copy quality)
# vs. visual (image-only QA quality).
_PACKAGE_WEIGHT = 0.55
_VISUAL_WEIGHT = 0.45


# ---------------------------------------------------------------------------
# §4.1 dataclasses and report schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OcrBox:
    """One OCR-detected text box, in the same pixel space as the image."""

    text: str
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class ThumbnailOcrResult:
    """Injected OCR output for one candidate image. Never fetched here."""

    boxes: tuple[OcrBox, ...] = ()

    @property
    def full_text(self) -> str:
        return " ".join(box.text for box in self.boxes)


@dataclass
class ThumbnailCandidateReport:
    """Serializable per-candidate QA report (schema_version pinned)."""

    variant_index: int
    path: str
    decode: dict
    package_score: float
    sibling_checks: list[dict] = field(default_factory=list)
    history_checks: list[dict] = field(default_factory=list)
    ocr_check: dict = field(default_factory=dict)
    visual_check: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @property
    def hard_failed(self) -> bool:
        if self.decode.get("status") == STATUS_FAIL:
            return True
        if self.ocr_check.get("status") == STATUS_FAIL:
            return True
        if self.visual_check.get("status") == STATUS_FAIL:
            return True
        if any(c.get("status") == STATUS_FAIL for c in self.sibling_checks):
            return True
        return any(c.get("status") == STATUS_FAIL for c in self.history_checks)

    @property
    def warning_count(self) -> int:
        checks = [self.ocr_check, self.visual_check, *self.sibling_checks, *self.history_checks]
        return sum(1 for c in checks if c.get("status") == STATUS_WARN)

    @property
    def requires_manual_review(self) -> bool:
        return bool(self.ocr_check.get("requires_manual_review"))

    @property
    def min_history_distance(self) -> float:
        distances = [c["distance"] for c in self.history_checks if "distance" in c]
        return min(distances) if distances else float("inf")

    def reason_codes(self) -> list[str]:
        codes: list[str] = []
        codes.extend(self.decode.get("reason_codes") or [])
        codes.extend(self.ocr_check.get("reason_codes") or [])
        codes.extend(self.visual_check.get("reason_codes") or [])
        for check in (*self.sibling_checks, *self.history_checks):
            if check.get("status") == STATUS_FAIL:
                codes.append(f"similarity_fail:{check.get('path')}")
        return codes


class ThumbnailQualityError(Exception):
    """Raised when every generated candidate fails hard QA."""

    def __init__(self, message: str, reasons: dict[int, list[str]]):
        super().__init__(message)
        self.reasons = reasons


# ---------------------------------------------------------------------------
# §4.2 image decode and dimensions
# ---------------------------------------------------------------------------

def decode_thumbnail_image(data: bytes) -> dict:
    """Decode image bytes. Never lets a Pillow/PIL exception escape."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            width, height = img.size
            mode = img.mode
            dhash = compute_dhash(img)
    except Exception:
        return {"status": STATUS_FAIL, "reason_codes": ["decode_error"]}

    reason_codes: list[str] = []
    status = STATUS_PASS
    if (width, height) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        status = STATUS_FAIL
        reason_codes.append("wrong_dimensions")

    return {
        "status": status,
        "reason_codes": reason_codes,
        "width": width,
        "height": height,
        "mode": mode,
        "dhash": dhash,
    }


# ---------------------------------------------------------------------------
# §4.3 dHash and similarity
# ---------------------------------------------------------------------------

def compute_dhash(img: Image.Image, hash_size: int = 8) -> int:
    small = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[row_start + col] > pixels[row_start + col + 1] else 0)
    return bits


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def compare_sibling_similarity(dhash_a: int, dhash_b: int, path_b: str) -> dict:
    distance = hamming_distance(dhash_a, dhash_b)
    return {
        "path": path_b,
        "distance": distance,
        "status": STATUS_FAIL if distance <= SIBLING_DHASH_MAX else STATUS_PASS,
    }


def compare_history_similarity(dhash_a: int, dhash_b: int, path_b: str) -> dict:
    distance = hamming_distance(dhash_a, dhash_b)
    return {
        "path": path_b,
        "distance": distance,
        "status": STATUS_FAIL if distance <= HISTORY_DHASH_MAX else STATUS_PASS,
    }


# ---------------------------------------------------------------------------
# §4.4 concept-signature difference count
# ---------------------------------------------------------------------------

def signature_difference_status(
    candidate_signature: dict | None,
    other_signature: dict | None,
    min_differences: int,
) -> dict:
    if not candidate_signature or not other_signature:
        return {"status": STATUS_NOT_AVAILABLE, "differences": None}
    keys = set(candidate_signature) | set(other_signature)
    differences = sum(1 for k in keys if candidate_signature.get(k) != other_signature.get(k))
    status = STATUS_PASS if differences >= min_differences else STATUS_FAIL
    return {"status": status, "differences": differences}


# ---------------------------------------------------------------------------
# §4.5 OCR exact-copy behavior
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_ocr_text(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _content_words(text: str) -> list[str]:
    return _PUNCT_RE.sub("", text).casefold().split()


def check_ocr_exact_copy(
    expected_text: str,
    ocr_result: ThumbnailOcrResult | None,
    image_width: int,
    image_height: int,
) -> dict:
    if ocr_result is None:
        return {
            "status": STATUS_NOT_RUN,
            "requires_manual_review": True,
            "reason_codes": ["ocr_not_run"],
        }

    reason_codes: list[str] = []
    raw_actual = ocr_result.full_text
    expected_norm = _normalize_ocr_text(expected_text)
    actual_norm = _normalize_ocr_text(raw_actual)

    status = STATUS_PASS
    if _content_words(expected_norm) != _content_words(actual_norm):
        status = STATUS_FAIL
        reason_codes.append("text_mismatch")
    elif expected_norm.casefold() != actual_norm.casefold():
        status = STATUS_WARN
        reason_codes.append("punctuation_only_difference")

    out_of_bounds = any(
        box.left < 0
        or box.top < 0
        or box.left + box.width > image_width
        or box.top + box.height > image_height
        for box in ocr_result.boxes
    )
    if out_of_bounds:
        status = STATUS_FAIL
        reason_codes.append("box_outside_image")

    frame_area = image_width * image_height
    total_box_area = sum(max(0.0, box.width) * max(0.0, box.height) for box in ocr_result.boxes)
    area_fraction = (total_box_area / frame_area) if frame_area else 0.0
    if area_fraction > _OCR_AREA_FAIL:
        status = STATUS_FAIL
        reason_codes.append("text_area_too_large")
    elif area_fraction > _OCR_AREA_WARN and status == STATUS_PASS:
        status = STATUS_WARN
        reason_codes.append("text_area_large")

    return {
        "status": status,
        "requires_manual_review": False,
        "reason_codes": reason_codes,
        "raw_expected": expected_text,
        "raw_actual": raw_actual,
        "area_fraction": area_fraction,
    }


# ---------------------------------------------------------------------------
# §4.6 visual heuristics — contrast/clutter only, no demographic inference.
# ---------------------------------------------------------------------------

def evaluate_visual_heuristics(img: Image.Image) -> dict:
    gray = img.convert("L")
    width, height = gray.size
    pixels = list(gray.getdata())
    if not pixels:
        return {
            "status": STATUS_FAIL,
            "reason_codes": ["empty_image"],
            "contrast_std": 0.0,
            "edge_density": 0.0,
            "contrast_threshold": _CONTRAST_WARN_STD,
            "edge_density_threshold": _EDGE_DENSITY_WARN,
        }

    mean = sum(pixels) / len(pixels)
    variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
    contrast_std = variance**0.5

    edge_count = 0
    total_pairs = 0
    for y in range(height):
        row_start = y * width
        for x in range(width - 1):
            total_pairs += 1
            if abs(pixels[row_start + x] - pixels[row_start + x + 1]) > _EDGE_DELTA_THRESHOLD:
                edge_count += 1
    edge_density = (edge_count / total_pairs) if total_pairs else 0.0

    reason_codes: list[str] = []
    status = STATUS_PASS
    if contrast_std < _CONTRAST_WARN_STD:
        # Low contrast is a strong warning, not a hard fail: a flat/minimalist
        # frame is not automatically unusable, and treating it as a hard
        # failure would block otherwise-valid candidates from ever winning
        # selection just for being visually calm.
        status = STATUS_WARN
        reason_codes.append("low_contrast")
    if edge_density > _EDGE_DENSITY_WARN:
        if status == STATUS_PASS:
            status = STATUS_WARN
        reason_codes.append("high_clutter")

    return {
        "status": status,
        "reason_codes": reason_codes,
        "contrast_std": contrast_std,
        "edge_density": edge_density,
        "contrast_threshold": _CONTRAST_WARN_STD,
        "edge_density_threshold": _EDGE_DENSITY_WARN,
    }


# ---------------------------------------------------------------------------
# §4.7 deterministic selection
# ---------------------------------------------------------------------------

def select_primary_candidate(reports: Sequence[ThumbnailCandidateReport]) -> dict:
    """Pick the best valid candidate. Raises ThumbnailQualityError if none."""
    valid = [r for r in reports if not r.hard_failed]
    if not valid:
        reasons = {r.variant_index: r.reason_codes() for r in reports}
        raise ThumbnailQualityError(
            "All generated thumbnail candidates failed QA", reasons
        )

    def _combined_score(r: ThumbnailCandidateReport) -> float:
        visual_score = r.visual_check.get("quality_score", 0.0)
        return _PACKAGE_WEIGHT * r.package_score + _VISUAL_WEIGHT * visual_score

    def _rank_key(r: ThumbnailCandidateReport):
        # Descending combined score; on a tie, fewer warnings wins, then the
        # candidate that is MORE different from recent history (larger
        # min-history distance = more novel), then lowest variant index.
        history_distance = r.min_history_distance
        novelty = -history_distance if history_distance != float("inf") else float("-inf")
        return (-_combined_score(r), r.warning_count, novelty, r.variant_index)

    ranked = sorted(valid, key=_rank_key)
    winner = ranked[0]
    return {
        "selected_variant_index": winner.variant_index,
        "selected_path": winner.path,
        "requires_manual_review": any(r.requires_manual_review for r in valid),
        "candidates": [r.variant_index for r in ranked],
    }
