"""Pure long-form thumbnail QA module — Task 4 of the packaging-CTR plan.

No provider calls, no filesystem history discovery here (that lives in the
orchestrator stage). Everything is deterministic and Pillow-only: image
decode/dimensions, dHash sibling/history similarity, concept-signature
difference counting, injected-OCR exact-copy checking, cheap visual
heuristics, and final deterministic candidate selection.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from video_agent.qa import thumbnail_package_qa as qa


def _jpeg_bytes(width: int, height: int, color=(120, 130, 140)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _checker_image(width: int, height: int, cell: int = 40) -> Image.Image:
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            on = ((x // cell) + (y // cell)) % 2 == 0
            pixels[x, y] = (240, 240, 240) if on else (10, 10, 10)
    return img


# ── §4.1 dataclasses and report schema ──────────────────────────────────────

def test_ocr_box_and_result_are_frozen_and_serializable():
    box = qa.OcrBox(text="HOLA", left=10.0, top=10.0, width=100.0, height=40.0)
    with pytest.raises(AttributeError):
        box.text = "ADIOS"  # frozen
    result = qa.ThumbnailOcrResult(boxes=(box,))
    assert result.full_text == "HOLA"


def test_schema_version_is_stable_string_constant():
    assert isinstance(qa.SCHEMA_VERSION, str)
    assert qa.SCHEMA_VERSION


def test_status_constants_cover_required_states():
    assert {qa.STATUS_PASS, qa.STATUS_FAIL, qa.STATUS_WARN, qa.STATUS_NOT_RUN, qa.STATUS_NOT_AVAILABLE} == {
        "pass", "fail", "warning", "not_run", "not_available",
    }


# ── §4.2 image decode and dimensions ────────────────────────────────────────

def test_valid_1920x1080_jpeg_passes_decode():
    result = qa.decode_thumbnail_image(_jpeg_bytes(1920, 1080))
    assert result["status"] == qa.STATUS_PASS
    assert result["width"] == 1920
    assert result["height"] == 1080


def test_1280x720_fails_exact_dimension_check_despite_correct_aspect_ratio():
    result = qa.decode_thumbnail_image(_jpeg_bytes(1280, 720))
    assert result["status"] == qa.STATUS_FAIL
    assert "wrong_dimensions" in result["reason_codes"]


def test_corrupt_bytes_fail_decode_with_reason_code_not_a_raised_exception():
    result = qa.decode_thumbnail_image(b"not an image at all")
    assert result["status"] == qa.STATUS_FAIL
    assert "decode_error" in result["reason_codes"]


def test_alpha_png_can_be_decoded_for_qa():
    img = Image.new("RGBA", (1920, 1080), (10, 20, 30, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = qa.decode_thumbnail_image(buf.getvalue())
    assert result["status"] == qa.STATUS_PASS
    assert result["mode"] == "RGBA"


# ── §4.3 dHash and similarity thresholds ─────────────────────────────────────

def test_identical_pixels_have_hamming_distance_zero():
    a = qa.decode_thumbnail_image(_jpeg_bytes(64, 64, (50, 60, 70)))
    b = qa.decode_thumbnail_image(_jpeg_bytes(64, 64, (50, 60, 70)))
    assert qa.hamming_distance(a["dhash"], b["dhash"]) == 0


def test_copied_image_with_metadata_change_remains_identical():
    original = _checker_image(64, 64)
    buf_a = io.BytesIO()
    original.save(buf_a, format="JPEG")
    buf_b = io.BytesIO()
    original.save(buf_b, format="JPEG", comment=b"different metadata")
    a = qa.decode_thumbnail_image(buf_a.getvalue())
    b = qa.decode_thumbnail_image(buf_b.getvalue())
    assert qa.hamming_distance(a["dhash"], b["dhash"]) == 0


def test_sibling_threshold_six_fails_at_six_and_passes_at_seven():
    fail_check = qa.compare_sibling_similarity(dhash_a=0b0, dhash_b=0b111111, path_b="sibling.jpg")
    pass_check = qa.compare_sibling_similarity(dhash_a=0b0, dhash_b=0b1111111, path_b="sibling.jpg")
    assert fail_check["distance"] == 6
    assert fail_check["status"] == qa.STATUS_FAIL
    assert pass_check["distance"] == 7
    assert pass_check["status"] == qa.STATUS_PASS
    assert fail_check["path"] == "sibling.jpg"


def test_history_threshold_five_fails_at_five_and_passes_at_six():
    fail_check = qa.compare_history_similarity(dhash_a=0b0, dhash_b=0b11111, path_b="history.jpg")
    pass_check = qa.compare_history_similarity(dhash_a=0b0, dhash_b=0b111111, path_b="history.jpg")
    assert fail_check["distance"] == 5
    assert fail_check["status"] == qa.STATUS_FAIL
    assert pass_check["distance"] == 6
    assert pass_check["status"] == qa.STATUS_PASS


def test_named_similarity_constants_match_spec():
    assert qa.SIBLING_DHASH_MAX == 6
    assert qa.HISTORY_DHASH_MAX == 5
    assert qa.SIBLING_SIGNATURE_MIN_DIFFERENCES == 5
    assert qa.HISTORY_SIGNATURE_MIN_DIFFERENCES == 3


# ── §4.4 signature difference count ─────────────────────────────────────────

@pytest.mark.parametrize("differences", [0, 2, 3, 5, 8])
def test_signature_difference_count_is_exact(differences):
    base = {f"k{i}": "same" for i in range(10)}
    other = dict(base)
    for i in range(differences):
        other[f"k{i}"] = "different"
    result = qa.signature_difference_status(base, other, min_differences=qa.SIBLING_SIGNATURE_MIN_DIFFERENCES)
    assert result["differences"] == differences


def test_missing_signature_metadata_is_not_available_not_a_pass():
    result = qa.signature_difference_status(None, {"a": 1}, min_differences=qa.SIBLING_SIGNATURE_MIN_DIFFERENCES)
    assert result["status"] == qa.STATUS_NOT_AVAILABLE
    assert result["status"] != qa.STATUS_PASS


# ── §7 provenance / no-false-pass regression (Task 7) ───────────────────────

def test_ocr_not_run_survives_json_round_trip_still_not_a_pass():
    """A serialized-then-deserialized not_run OCR result must never resolve
    to a pass — no downstream reader can be tricked by a JSON round trip."""
    result = qa.check_ocr_exact_copy("HOLA", None, 1920, 1080)
    round_tripped = json.loads(json.dumps(result, ensure_ascii=False))
    assert round_tripped["status"] == qa.STATUS_NOT_RUN
    assert round_tripped["status"] != qa.STATUS_PASS
    assert round_tripped["requires_manual_review"] is True


def test_missing_signature_survives_json_round_trip_still_not_a_pass():
    result = qa.signature_difference_status(None, {"a": 1}, min_differences=qa.SIBLING_SIGNATURE_MIN_DIFFERENCES)
    round_tripped = json.loads(json.dumps(result))
    assert round_tripped["status"] == qa.STATUS_NOT_AVAILABLE
    assert round_tripped["status"] != qa.STATUS_PASS


def test_candidate_report_serializes_with_dataclasses_asdict_and_stable_schema():
    import dataclasses
    report = _report(1)
    payload = json.loads(json.dumps(dataclasses.asdict(report), ensure_ascii=False))
    assert payload["schema_version"] == qa.SCHEMA_VERSION
    assert payload["variant_index"] == 1


# ── §4.5 OCR exact-copy behavior ─────────────────────────────────────────────

def _ocr(text: str, left=100.0, top=100.0, width=400.0, height=100.0) -> qa.ThumbnailOcrResult:
    return qa.ThumbnailOcrResult(boxes=(qa.OcrBox(text=text, left=left, top=top, width=width, height=height),))


def test_ocr_exact_spanish_accents_and_punctuation_passes():
    result = qa.check_ocr_exact_copy("¿DUERMES MEJOR?", _ocr("¿DUERMES MEJOR?"), 1920, 1080)
    assert result["status"] == qa.STATUS_PASS


def test_ocr_normalized_spacing_and_case_passes_but_raw_values_kept():
    result = qa.check_ocr_exact_copy("DUERME MEJOR HOY", _ocr("duerme   mejor hoy"), 1920, 1080)
    assert result["status"] == qa.STATUS_PASS
    assert result["raw_actual"] == "duerme   mejor hoy"
    assert result["raw_expected"] == "DUERME MEJOR HOY"


def test_ocr_missing_content_word_fails():
    result = qa.check_ocr_exact_copy("DUERME MEJOR HOY", _ocr("DUERME HOY"), 1920, 1080)
    assert result["status"] == qa.STATUS_FAIL


def test_ocr_substituted_accented_word_fails_when_it_changes_content():
    result = qa.check_ocr_exact_copy("MENOS SAL", _ocr("MENOS SOL"), 1920, 1080)
    assert result["status"] == qa.STATUS_FAIL


def test_ocr_material_extra_text_fails():
    result = qa.check_ocr_exact_copy("DUERME MEJOR", _ocr("DUERME MEJOR Y VIVE FELIZ"), 1920, 1080)
    assert result["status"] == qa.STATUS_FAIL


def test_ocr_punctuation_only_difference_warns():
    result = qa.check_ocr_exact_copy("DUERMES MEJOR", _ocr("¿DUERMES MEJOR?"), 1920, 1080)
    assert result["status"] == qa.STATUS_WARN


def test_ocr_box_outside_image_fails():
    box = qa.OcrBox(text="HOLA", left=1800.0, top=100.0, width=400.0, height=100.0)
    result = qa.check_ocr_exact_copy("HOLA", qa.ThumbnailOcrResult(boxes=(box,)), 1920, 1080)
    assert result["status"] == qa.STATUS_FAIL
    assert "box_outside_image" in result["reason_codes"]


def test_ocr_total_box_area_boundaries():
    # 1920x1080 frame; a full-width box of height H covers H/1080 of the frame.
    passing = qa.check_ocr_exact_copy(
        "HOLA", _ocr("HOLA", left=0.0, top=0.0, width=1920.0, height=1080 * 0.35), 1920, 1080
    )
    warning = qa.check_ocr_exact_copy(
        "HOLA", _ocr("HOLA", left=0.0, top=0.0, width=1920.0, height=1080 * 0.38), 1920, 1080
    )
    failing = qa.check_ocr_exact_copy(
        "HOLA", _ocr("HOLA", left=0.0, top=0.0, width=1920.0, height=1080 * 0.41), 1920, 1080
    )
    assert passing["status"] == qa.STATUS_PASS
    assert warning["status"] == qa.STATUS_WARN
    assert failing["status"] == qa.STATUS_FAIL


def test_ocr_none_is_not_run_and_requires_manual_review_never_passes():
    result = qa.check_ocr_exact_copy("HOLA", None, 1920, 1080)
    assert result["status"] == qa.STATUS_NOT_RUN
    assert result["requires_manual_review"] is True
    assert result["status"] != qa.STATUS_PASS


# ── §4.6 visual heuristics ───────────────────────────────────────────────────

def test_flat_image_fails_or_warns_contrast():
    flat = Image.new("RGB", (64, 64), (128, 128, 128))
    result = qa.evaluate_visual_heuristics(flat)
    assert result["status"] in {qa.STATUS_FAIL, qa.STATUS_WARN}
    assert "low_contrast" in result["reason_codes"]


def test_high_contrast_clean_subject_passes():
    img = Image.new("RGB", (64, 64), (250, 250, 250))
    pixels = img.load()
    for y in range(20, 44):
        for x in range(20, 44):
            pixels[x, y] = (10, 10, 10)
    result = qa.evaluate_visual_heuristics(img)
    assert result["status"] == qa.STATUS_PASS


def test_excessive_edge_density_warns_clutter():
    result = qa.evaluate_visual_heuristics(_checker_image(64, 64, cell=2))
    assert "high_clutter" in result["reason_codes"]


def test_visual_heuristic_records_thresholds_and_actual_values():
    result = qa.evaluate_visual_heuristics(_checker_image(64, 64))
    assert "contrast_std" in result
    assert "edge_density" in result
    assert "contrast_threshold" in result
    assert "edge_density_threshold" in result


def test_visual_heuristics_never_reference_skin_face_or_demographics():
    import inspect
    import re as _re
    source = inspect.getsource(qa.evaluate_visual_heuristics).lower()
    for banned in (r"\bskin\b", r"\bface\b", r"\bages?\b", r"\bgender\b"):
        assert not _re.search(banned, source), banned


# ── §4.7 deterministic selection ────────────────────────────────────────────

def _report(
    variant_index: int,
    package_score: float = 70.0,
    visual_status: str = qa.STATUS_PASS,
    quality_score: float = 70.0,
    decode_status: str = qa.STATUS_PASS,
    ocr_status: str = qa.STATUS_PASS,
    requires_manual_review: bool = False,
    warning_count: int = 0,
    min_history_distance: float = 40.0,
) -> qa.ThumbnailCandidateReport:
    sibling_checks = []
    history_checks = [{"path": "h.jpg", "distance": min_history_distance, "status": qa.STATUS_PASS}]
    ocr_check = {
        "status": ocr_status,
        "requires_manual_review": requires_manual_review,
        "reason_codes": [] if ocr_status == qa.STATUS_PASS else ["fixture_ocr_reason"],
    }
    visual_check = {
        "status": visual_status,
        "quality_score": quality_score,
        "reason_codes": [] if visual_status == qa.STATUS_PASS else ["fixture_visual_reason"],
    }
    for _ in range(warning_count):
        history_checks.append({"path": "w.jpg", "distance": 50.0, "status": qa.STATUS_WARN})
    return qa.ThumbnailCandidateReport(
        variant_index=variant_index,
        path=f"variant_{variant_index}.jpg",
        decode={
            "status": decode_status,
            "reason_codes": [] if decode_status == qa.STATUS_PASS else ["fixture_decode_reason"],
        },
        package_score=package_score,
        sibling_checks=sibling_checks,
        history_checks=history_checks,
        ocr_check=ocr_check,
        visual_check=visual_check,
    )


def test_any_hard_failed_candidate_is_excluded():
    reports = [
        _report(1, package_score=99, decode_status=qa.STATUS_FAIL),
        _report(2, package_score=50),
    ]
    result = qa.select_primary_candidate(reports)
    assert result["selected_variant_index"] == 2


def test_selection_uses_weighted_package_and_visual_score():
    reports = [
        _report(1, package_score=60.0, quality_score=60.0),  # combined 60
        _report(2, package_score=90.0, quality_score=90.0),  # combined 90 — wins
        _report(3, package_score=50.0, quality_score=50.0),
    ]
    result = qa.select_primary_candidate(reports)
    assert result["selected_variant_index"] == 2


def test_variant_two_can_win():
    reports = [_report(1, package_score=40), _report(2, package_score=95), _report(3, package_score=40)]
    assert qa.select_primary_candidate(reports)["selected_variant_index"] == 2


def test_variant_three_can_win():
    reports = [_report(1, package_score=40), _report(2, package_score=40), _report(3, package_score=95)]
    assert qa.select_primary_candidate(reports)["selected_variant_index"] == 3


def test_tie_breaks_by_warning_count_then_variant_index():
    reports = [
        _report(1, package_score=70, quality_score=70, warning_count=2),
        _report(2, package_score=70, quality_score=70, warning_count=0),
        _report(3, package_score=70, quality_score=70, warning_count=1),
    ]
    assert qa.select_primary_candidate(reports)["selected_variant_index"] == 2


def test_all_hard_failed_raises_quality_error_with_per_candidate_reasons():
    reports = [
        _report(1, decode_status=qa.STATUS_FAIL),
        _report(2, ocr_status=qa.STATUS_FAIL),
        _report(3, visual_status=qa.STATUS_FAIL),
    ]
    with pytest.raises(qa.ThumbnailQualityError) as exc_info:
        qa.select_primary_candidate(reports)
    assert set(exc_info.value.reasons.keys()) == {1, 2, 3}
    assert all(exc_info.value.reasons[i] for i in (1, 2, 3))


def test_ocr_not_run_candidate_can_win_but_report_requires_manual_review():
    reports = [
        _report(1, package_score=95, ocr_status=qa.STATUS_NOT_RUN, requires_manual_review=True),
        _report(2, package_score=40),
    ]
    result = qa.select_primary_candidate(reports)
    assert result["selected_variant_index"] == 1
    assert result["requires_manual_review"] is True
