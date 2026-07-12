"""Tests for server-side YouTube chapter timestamp recomputation."""

from __future__ import annotations

import json
from pathlib import Path

from video_agent.operator import (
    _compute_chapter_timestamps,
    _normalize_seo_candidate,
    _rewrite_description_chapters,
)


def _scene(idx: int, duration: float, on_screen_text: str = "", narration: str = "") -> dict:
    return {
        "id": f"scene-{idx:02d}",
        "duration_sec": duration,
        "on_screen_text": on_screen_text,
        "narration": narration,
    }


def _scene_doc(total: float, scenes: list[dict]) -> dict:
    return {"total_duration_sec": total, "scenes": scenes}


def test_compute_chapter_timestamps_first_chapter_is_zero():
    doc = _scene_doc(120, [_scene(i, 12, f"Topic {i}") for i in range(1, 11)])
    chapters = _compute_chapter_timestamps(doc, None)
    assert chapters[0][0] == "00:00"


def test_compute_chapter_timestamps_never_exceeds_total_duration():
    total = 493  # 8 min 13 s — matches the real bug report
    doc = _scene_doc(total, [_scene(i, total / 48, f"Topic {i}") for i in range(1, 49)])

    chapters = _compute_chapter_timestamps(doc, None)
    for ts, _ in chapters:
        m, s = ts.split(":")
        seconds = int(m) * 60 + int(s)
        assert seconds <= total, f"chapter {ts} exceeds total duration {total}"


def test_compute_chapter_timestamps_uses_script_sections_when_available():
    doc = _scene_doc(600, [_scene(i, 60, f"Scene {i}") for i in range(1, 11)])
    script = {
        "sections": [
            {"title": "Introducción"},
            {"title": "Activadores de la tarde"},
            {"title": "Cena ligera"},
            {"title": "Pendientes"},
            {"title": "Cierre"},
        ]
    }
    chapters = _compute_chapter_timestamps(doc, script)
    titles = [t for _, t in chapters]
    assert "Introducción" in titles
    assert "Cena ligera" in titles


def test_compute_chapter_timestamps_handles_missing_durations():
    chapters = _compute_chapter_timestamps({"scenes": []}, None)
    assert chapters == []
    chapters = _compute_chapter_timestamps(None, None)
    assert chapters == []


def test_rewrite_description_chapters_replaces_inline_run():
    desc = (
        "Intro paragraph.\n\n"
        "00:00 - Wrong A 01:30 - Wrong B 03:00 - Wrong C 13:20 - Way too far\n\n"
        "Suscríbete."
    )
    chapters = [("00:00", "Inicio"), ("01:30", "Activadores"), ("04:00", "Cierre")]
    out = _rewrite_description_chapters(desc, chapters)
    assert "00:00 - Inicio" in out
    assert "01:30 - Activadores" in out
    assert "04:00 - Cierre" in out
    # Old fabricated timestamps must be gone.
    assert "13:20" not in out
    # Each chapter on its own line.
    assert "00:00 - Inicio\n01:30 - Activadores\n04:00 - Cierre" in out


def test_normalize_seo_candidate_replaces_fabricated_timestamps_with_real_ones():
    # Simulates the production bug: 9-minute video, ChatGPT wrote 13:20 chapter.
    scenes = []
    cursor = 0.0
    total = 540.0  # exactly 9 minutes
    for i in range(1, 49):
        scenes.append(_scene(i, total / 48, f"Tema {i}"))
        cursor += total / 48
    doc = _scene_doc(total, scenes)

    candidate = {
        "title": "Cómo dormir mejor",
        "description": (
            "Dormir mejor puede empezar antes.\n\n"
            "00:00 - Intro 01:30 - Activadores 13:20 - Plan mínimo\n\n"
            "Suscríbete."
        ),
        "tags": ["a"],
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }
    normalized = _normalize_seo_candidate(candidate, scene_doc=doc)
    desc = normalized["description"]
    # Real total = 540s = 09:00 — no chapter may exceed that.
    for token in desc.split():
        if ":" in token and token.replace(":", "").isdigit():
            m, s = token.split(":")
            assert int(m) * 60 + int(s) <= total, f"chapter {token} exceeds {total}"
    # Each chapter line must stand on its own row (preserves YouTube parsing).
    chapter_lines = [line for line in desc.splitlines() if line.strip().startswith(("00:", "01:", "02:", "03:", "04:", "05:", "06:", "07:", "08:", "09:"))]
    assert len(chapter_lines) >= 3
    # Bug fingerprint string must be gone.
    assert "13:20" not in desc


def test_compute_chapter_timestamps_anchors_sections_to_matching_scenes():
    """Chapter boundaries must land on the scene where the section's content
    actually starts (matched via narration), not on evenly spaced indices.

    Mirrors bug-528's secondary flaw: with even spacing, 'Cafeína con horario'
    landed minutes away from where the cafeína content begins.
    """
    scenes = [
        _scene(1, 10, narration="Si un partido nocturno te deja sin fuerzas, prepáralo mejor."),
        _scene(2, 10, narration="Elige el partido que de verdad quieres ver en el calendario."),
        _scene(3, 10, narration="Protege las noches de alrededor y decide con ilusión."),
        _scene(4, 10, narration="Evita convertir una noche bonita en una cadena de cansancio."),
        _scene(5, 10, narration="Una siesta corta de quince a treinta minutos da un respiro."),
        _scene(6, 10, narration="Pon alarma para que la siesta no se convierta en sueño profundo."),
        _scene(7, 10, narration="Otro punto clave es la cafeína y su horario límite."),
        _scene(8, 10, narration="El café de media tarde tapa la señal de cansancio."),
        _scene(9, 10, narration="La cena también decide parte de la noche, mejor ligera."),
        _scene(10, 10, narration="Una cena sencilla con proteína ligera unas horas antes."),
        _scene(11, 10, narration="Al final, reduce la luz intensa y las pantallas al terminar."),
        _scene(12, 10, narration="Diez minutos de cierre sin pantalla recuperan la noche."),
    ]
    script = {
        "sections": [
            {"title": "Elige tu partido clave", "focus": "Decidir qué encuentros merecen alterar el descanso."},
            {"title": "Siesta corta y bien colocada", "focus": "Usar una siesta de 15 a 30 minutos sin sueño profundo."},
            {"title": "Cafeína con horario", "focus": "Ajustar café y refrescos según la hora del encuentro."},
            {"title": "Cena ligera sin quedarse corto", "focus": "Preparar una cena equilibrada que evite pesadez."},
            {"title": "Pantallas y luz al terminar", "focus": "Reducir luz intensa y estímulos al acabar el partido."},
        ]
    }
    chapters = _compute_chapter_timestamps(_scene_doc(120, scenes), script)
    got = dict((title, ts) for ts, title in chapters)
    assert got["Elige tu partido clave"] == "00:00"
    assert got["Siesta corta y bien colocada"] == "00:40"  # scene 5
    assert got["Cafeína con horario"] == "01:00"  # scene 7
    assert got["Cena ligera sin quedarse corto"] == "01:20"  # scene 9
    assert got["Pantallas y luz al terminar"] == "01:40"  # scene 11


def test_compute_chapter_timestamps_falls_back_to_even_spacing_without_matches():
    """Sections whose vocabulary never appears in any narration cannot be
    anchored — keep the legacy evenly spaced boundaries instead of guessing."""
    scenes = [_scene(i, 60, narration=f"Contenido genérico número {i}.") for i in range(1, 11)]
    script = {
        "sections": [
            {"title": "Introducción"},
            {"title": "Activadores"},
            {"title": "Cierre"},
        ]
    }
    chapters = _compute_chapter_timestamps(_scene_doc(600, scenes), script)
    assert chapters[0] == ("00:00", "Introducción")
    # Legacy behavior: >1 chapter, all within total, titles preserved.
    assert len(chapters) >= 3
    titles = [t for _, t in chapters]
    assert "Activadores" in titles
    assert "Cierre" in titles


def test_compute_chapter_timestamps_anchored_respects_min_gap_and_order():
    """Anchored chapters must be strictly increasing and at least 10s apart
    (YouTube rejects chapter blocks with shorter chapters)."""
    scenes = [
        _scene(1, 5, narration="Introducción al tema del descanso nocturno."),
        _scene(2, 5, narration="La siesta corta ayuda mucho por la tarde."),
        _scene(3, 5, narration="La cafeína tiene horario límite razonable."),
        _scene(4, 60, narration="La cena ligera cierra el plan de la noche."),
    ]
    script = {
        "sections": [
            {"title": "Descanso nocturno", "focus": "descanso nocturno"},
            {"title": "Siesta corta", "focus": "siesta tarde"},
            {"title": "Cafeína", "focus": "cafeína horario"},
            {"title": "Cena ligera", "focus": "cena ligera noche"},
        ]
    }
    chapters = _compute_chapter_timestamps(_scene_doc(75, scenes), script)
    seconds = []
    for ts, _ in chapters:
        m, s = ts.split(":")
        seconds.append(int(m) * 60 + int(s))
    assert seconds == sorted(seconds)
    gaps = [b - a for a, b in zip(seconds, seconds[1:], strict=False)]
    assert all(g >= 10 for g in gaps), f"chapter gaps below 10s: {gaps}"


def test_normalize_seo_candidate_without_scene_doc_keeps_existing_chapters():
    """Backward compatibility: callers that don't pass scenes still get plain normalization."""
    candidate = {
        "title": "Sin escenas",
        "description": "Intro.\n\n00:00 - A\n01:30 - B\n\nCTA.",
        "tags": ["a"],
        "language": "es-ES",
        "ai_disclosure": True,
        "thumbnail_path": "thumbnail.jpg",
    }
    normalized = _normalize_seo_candidate(candidate)  # scene_doc defaults to None
    assert "00:00 - A" in normalized["description"]
    assert "01:30 - B" in normalized["description"]


# ── bug-529: real-job regression (Mundial) ───────────────────────────────────
# Fixture distilled VERBATIM from
# jobs/como-se-pueden-disfrutar-los-partidos-nocturnos-del-mundial-*: real
# scene narrations, whisper audio offsets, script sections and the planned
# batch->section map. Nothing synthetic, no keyword-perfect scenes.

_FIX = Path(__file__).parent / "fixtures" / "mundial_chapters"

# Audited ground truth (codex bridge task 20260711-211607): section start
# scenes read from the real narration. Timestamps derive from those scenes'
# whisper offsets; sub-second display rounding may differ by <=1s from the
# audit list, so assertions allow that single second while pinning the scene.
_MUNDIAL_GROUND_TRUTH = [
    ("00:00", "Elige tu partido clave", 0.0),
    ("01:51", "Plan de energía, no de aguante", 111.5),
    ("03:08", "Siesta corta y bien colocada", 187.95),
    ("04:24", "Cafeína con horario", 263.79),
    ("05:39", "Cena ligera sin quedarse corto", 339.0),
    ("07:06", "Pantallas y luz al terminar", 426.5),
    ("08:20", "Bajada de revoluciones", 500.5),
    ("09:42", "Por qué pesa más tras los 45", 581.58),
    ("10:55", "Recuperar al día siguiente", 655.06),
    ("13:17", "Cuándo pedir ayuda", 796.52),
    ("14:09", "Estrategia reutilizable", 849.46),
]


def _mmss_to_sec(ts: str) -> int:
    m, s = ts.split(":")
    return int(m) * 60 + int(s)


def _mundial_inputs():
    scene_doc = json.loads((_FIX / "scenes.json").read_text(encoding="utf-8"))
    fix = json.loads((_FIX / "script.json").read_text(encoding="utf-8"))
    script = {"sections": fix["sections"]}
    plan = {"data": {"batches": fix["scene_batches"]}}
    return scene_doc, script, plan


def test_mundial_real_job_all_eleven_sections_survive_with_invariants():
    """The old code returned 10 chapters (hard cap dropped 'Estrategia
    reutilizable' at 14:09) with wrong anchors (00:30 for Plan, 08:55 for Por
    qué pesa). All 11 real sections must survive, in order, first at 00:00,
    strictly increasing, >=10s apart, never past the real duration."""
    scene_doc, script, plan = _mundial_inputs()
    chapters = _compute_chapter_timestamps(scene_doc, script, scenes_plan=plan)

    assert [t for _, t in chapters] == [t for _, t, _ in _MUNDIAL_GROUND_TRUTH]
    assert chapters[0][0] == "00:00"
    total = max(
        s["audio_offset_sec"] + s["duration_sec"] for s in scene_doc["scenes"]
    )
    last = -10.0
    for ts, _title in chapters:
        sec = _mmss_to_sec(ts)
        assert sec - last >= 10, (ts, last)
        assert sec < total
        last = sec


def test_mundial_real_job_anchors_track_the_audited_boundaries():
    """Every chapter lands near its audited boundary, and the boundaries the
    lexical signal can pin down exactly stay pinned (regression floor: the
    pre-fix output was minutes away — 00:30 vs 01:51, 08:55 vs 09:42 — and
    fabricated 21:55 for a 15:48 video)."""
    scene_doc, script, plan = _mundial_inputs()
    chapters = _compute_chapter_timestamps(scene_doc, script, scenes_plan=plan)

    exact = 0
    for (ts, _title), (_gt_ts, _gt_title, gt_off) in zip(chapters, _MUNDIAL_GROUND_TRUTH, strict=True):
        delta = abs(_mmss_to_sec(ts) - gt_off)
        assert delta <= 45, (ts, _gt_ts, delta)
        exact += delta <= 1.5
    assert exact >= 7, f"only {exact}/11 boundaries exact"
    # The two boundaries the previous algorithm got wrong by minutes:
    by_title = {t: ts for ts, t in chapters}
    assert abs(_mmss_to_sec(by_title["Por qué pesa más tras los 45"]) - 581.58) <= 1.5
    assert abs(_mmss_to_sec(by_title["Estrategia reutilizable"]) - 849.46) <= 1.5


def test_scene_section_metadata_gives_exact_deterministic_anchors():
    """PRIMARY path going forward: when scenes carry explicit section
    attribution, every boundary is the tagged scene's real audio offset — no
    lexical guessing. Tags here mirror the audited section spans."""
    scene_doc, script, _plan = _mundial_inputs()
    starts = {  # audited: section index -> first scene id
        0: 1, 1: 11, 2: 17, 3: 22, 4: 26, 5: 31, 6: 36, 7: 41, 8: 45, 9: 52, 10: 54,
    }
    titles = [s["title"] for s in script["sections"]]
    bounds = sorted((first, k) for k, first in starts.items())
    for scene in scene_doc["scenes"]:
        n = int(scene["id"].split("-")[1])
        current = max(k for first, k in bounds if first <= n)
        scene["section"] = titles[current]

    chapters = _compute_chapter_timestamps(scene_doc, script)

    assert [t for _, t in chapters] == titles
    for (ts, _t), (_gt, _title, gt_off) in zip(chapters[1:], _MUNDIAL_GROUND_TRUTH[1:], strict=True):
        assert abs(_mmss_to_sec(ts) - gt_off) <= 1.0


def test_chapter_overrides_artifact_wins_when_valid():
    """json/chapter_overrides.json is the audited correction channel for
    already-rendered jobs; it must be validated (00:00 first, increasing,
    >=10s, inside the video) and never invented."""
    scene_doc, script, plan = _mundial_inputs()
    overrides = {"chapters": [[ts, t] for ts, t, _ in _MUNDIAL_GROUND_TRUTH]}
    chapters = _compute_chapter_timestamps(
        scene_doc, script, scenes_plan=plan, chapter_overrides=overrides
    )
    assert chapters == [(ts, t) for ts, t, _ in _MUNDIAL_GROUND_TRUTH]

    # invalid overrides (not increasing) are IGNORED, not trusted
    bad = {"chapters": [["00:00", "a"], ["05:00", "b"], ["04:00", "c"]]}
    chapters = _compute_chapter_timestamps(
        scene_doc, script, scenes_plan=plan, chapter_overrides=bad
    )
    assert [t for _, t in chapters] == [t for _, t, _ in _MUNDIAL_GROUND_TRUTH]


def test_offsets_prefer_whisper_audio_over_planned_durations():
    """Planned duration_sec drifts from real audio (930s planned vs 948.5s
    rendered on the Mundial job); audio_offset_sec is the viewer's truth."""
    scene_doc, script, plan = _mundial_inputs()
    chapters = _compute_chapter_timestamps(scene_doc, script, scenes_plan=plan)
    by_title = dict((t, ts) for ts, t in chapters)
    # scene-52 planned-cumulative start differs from its whisper offset by
    # >5s; the chapter must sit on the whisper offset.
    assert abs(_mmss_to_sec(by_title["Cuándo pedir ayuda"]) - 796.52) <= 1.5


# ── bug-529 round 2: section attribution through the REAL batched path ────────

def test_sharded_batch_prompt_requires_verbatim_section_per_scene():
    """The production scene generator is the SHARDED path
    (_chatgpt_scenes_batch_prompt), not the single-shot prompt — it must demand
    the verbatim script section title on every scene."""
    from video_agent.operator_prompts import _chatgpt_scenes_batch_prompt

    channel_config = {"channel": {"id": "vida-plena-45"}}
    script = {
        "hook": "h",
        "narration": "n",
        "cta": "c",
        "sections": [{"title": "Elige tu partido clave", "focus": "f"}],
    }
    plan_envelope = {"data": {"target_scene_count": 12, "batches": []}}
    batch = {
        "batch_index": 1,
        "scene_start": "scene-01",
        "scene_end": "scene-06",
        "script_sections": ["Hook", "Elige tu partido clave"],
    }
    prompt = _chatgpt_scenes_batch_prompt(channel_config, script, plan_envelope, batch)

    assert "layout_reason, section" in prompt
    assert "VERBATIM" in prompt
    assert "script_sections" in prompt


def test_merged_batches_preserve_scene_section_end_to_end():
    """Assembled scenes must carry each shard scene's section attribution
    through merge_scene_batches AND the operator scenes normalizer — the whole
    path chapters depend on."""
    from video_agent.operator import _normalize_scenes_candidate
    from video_agent.operator_shards import merge_scene_batches

    def _scene(n: int, section: str) -> dict:
        return {
            "id": f"scene-{n:02d}",
            "duration_sec": 12,
            "narration": f"texto {n}",
            "on_screen_text": "T",
            "caption": "c",
            "visual_prompt": "warm kitchen",
            "motion": "push_in",
            "asset_refs": {},
            "layout": "single_focus",
            "layout_payload": {"title": "", "body": "", "bullets": [], "cta": ""},
            "layout_reason": "fits",
            "section": section,
        }

    def _envelope(idx: int, scenes: list[dict]) -> dict:
        return {
            "artifact_type": "scenes_batch",
            "schema_version": "2026-05-json-shards-v1",
            "job_id": "job-1",
            "channel_id": "vida-plena-45",
            "status": "complete",
            "batch_index": idx,
            "batch_total": 2,
            "warnings": [],
            "data": {"batch_index": idx, "batch_total": 2, "scenes": scenes},
        }

    merged = merge_scene_batches(
        job_id="job-1",
        channel_id="vida-plena-45",
        batch_envelopes=[
            _envelope(1, [_scene(1, "Hook"), _scene(2, "Elige tu partido clave")]),
            _envelope(2, [_scene(3, "Plan de energía"), _scene(4, "CTA")]),
        ],
    )
    sections = [s.get("section") for s in merged["scenes"]]
    assert sections == ["Hook", "Elige tu partido clave", "Plan de energía", "CTA"]

    normalized = _normalize_scenes_candidate(dict(merged))
    sections = [s.get("section") for s in normalized["scenes"]]
    assert sections == ["Hook", "Elige tu partido clave", "Plan de energía", "CTA"]


# ── bug-531: chapters resynced against the FINAL audio-fit timeline ──────────

def test_resync_seo_chapters_replaces_planned_timeline_overshoot(tmp_path):
    """Live repro (vida-sana job): the whisper-stage resync ran while
    scenes.json still carried PLANNED durations (sum ~24.6min); the render
    stage audio-fits the timeline to ~19.8min afterwards, so shipped chapters
    overshot the real video end (24:39 on a 20:03 video). The render-path
    resync must rewrite chapters from the FINAL scene_doc, through the
    race-safe single-field update (thumbnail_path preserved)."""
    from video_agent.operator import resync_seo_chapters

    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "script.json").write_text(json.dumps({
        "sections": [{"title": "Primera parte", "focus": "f"},
                     {"title": "Segunda parte", "focus": "f"}],
    }), encoding="utf-8")
    stale_desc = (
        "Resumen del vídeo.\n\n"
        "00:00 - Primera parte\n24:39 - Segunda parte\n\n"
        "Despedida."
    )
    (job_dir / "json" / "seo.json").write_text(json.dumps({
        "description": stale_desc,
        "thumbnail_path": "jobs/x/outputs/thumbnail_1.jpg",
    }, ensure_ascii=False), encoding="utf-8")

    # FINAL audio-fit timeline (in memory, as the render path holds it).
    final_scene_doc = {
        "total_duration_sec": 120,
        "scenes": [
            {"id": "scene-01", "duration_sec": 60, "audio_offset_sec": 0.0,
             "narration": "n", "section": "Primera parte"},
            {"id": "scene-02", "duration_sec": 60, "audio_offset_sec": 60.0,
             "narration": "n", "section": "Segunda parte"},
        ],
    }

    chapters = resync_seo_chapters(job_dir, scene_doc=final_scene_doc)

    assert chapters == [("00:00", "Primera parte"), ("01:00", "Segunda parte")]
    seo = json.loads((job_dir / "json" / "seo.json").read_text(encoding="utf-8"))
    assert "24:39" not in seo["description"]
    assert "01:00 - Segunda parte" in seo["description"]
    # single-field update: unrelated fields untouched
    assert seo["thumbnail_path"] == "jobs/x/outputs/thumbnail_1.jpg"
