"""Tests for the long-form ``graphic_images`` stage.

Generates a ChatGPT image for each graphic-layout scene (checklist/warning/quote/
cta) and records ``scene.graphic.image_ref``. Non-graphic scenes are untouched; a
single image failure is non-fatal (the scene falls back, the pipeline continues).
The image generator is injected so tests run without the live browser provider.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from video_agent.orchestrator.orchestrator import create_job
from video_agent.orchestrator.stages.graphic_images import _content_lines, run_graphic_images_stage


@pytest.fixture(autouse=True)
def _no_late_recovery_wait(monkeypatch):
    """Tests must not sit in the 180s late-recovery window on failures."""
    monkeypatch.setenv("GRAPHIC_LATE_RECOVERY_WINDOW_SEC", "0")


def _make_job(tmp_path: Path, scenes: list[dict], *, topic_accent_color: str | None = None) -> Path:
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "scenes.json").write_text(json.dumps({"job_id": "j1", "scenes": scenes}))
    if topic_accent_color:
        (job_dir / "json" / "seo.json").write_text(
            json.dumps({"topic_accent_color": topic_accent_color})
        )
    create_job(job_dir, "j1", "vida-plena-45", "json/idea.json", stages=["graphic_images"])
    return job_dir


def _fake_image_fn(written: list[str]):
    async def _fn(*, prompt: str, project_name: str, out_path: str):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")  # minimal stand-in
        written.append(out_path)
        return {"src": "https://chatgpt/img", "bytes": 6}
    return _fn


def test_generates_images_for_graphic_scenes_only(tmp_path):
    scenes = [
        {"id": "scene-01", "layout": "hook", "visual_prompt": "p", "asset_refs": {"background": "a.mp4"}},
        {"id": "scene-02", "layout": "checklist", "graphic": {"needed": True, "prompt": "a checklist"}},
        {"id": "scene-03", "layout": "subtitle", "visual_prompt": "p"},
        {"id": "scene-04", "layout": "cta", "graphic": {"needed": True, "prompt": "a cta"}},
    ]
    job_dir = _make_job(tmp_path, scenes)
    written: list[str] = []
    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))
    doc = json.loads(out.read_text())["scenes"]
    by_id = {s["id"]: s for s in doc}
    assert by_id["scene-02"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-02.png"
    assert by_id["scene-04"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-04.png"
    # hook is now a graphic layout too (gen image, attention-grabbing) — gets an image
    assert by_id["scene-01"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-01.png"
    # subtitle (non-graphic) gets no graphic image
    assert "graphic" not in by_id["scene-03"] or "image_ref" not in by_id["scene-03"].get("graphic", {})
    assert len(written) == 3


def test_graphic_needed_false_is_skipped(tmp_path):
    scenes = [{"id": "scene-01", "layout": "warning", "graphic": {"needed": False, "prompt": "x"}}]
    job_dir = _make_job(tmp_path, scenes)
    written: list[str] = []
    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))
    doc = json.loads(out.read_text())["scenes"]
    assert "image_ref" not in doc[0].get("graphic", {})
    assert written == []


def test_image_failure_is_non_fatal(tmp_path):
    scenes = [
        {"id": "scene-01", "layout": "checklist", "graphic": {"needed": True, "prompt": "boom"}},
        {"id": "scene-02", "layout": "quote", "graphic": {"needed": True, "prompt": "ok"}},
    ]
    job_dir = _make_job(tmp_path, scenes)
    written: list[str] = []
    good = _fake_image_fn(written)

    async def _flaky(*, prompt, project_name, out_path):
        if "boom" in prompt:
            raise RuntimeError("provider error")
        return await good(prompt=prompt, project_name=project_name, out_path=out_path)

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _flaky))
    doc = json.loads(out.read_text())["scenes"]
    by_id = {s["id"]: s for s in doc}
    assert "image_ref" not in by_id["scene-01"].get("graphic", {})  # failed, fell back
    assert by_id["scene-02"]["graphic"]["image_ref"] == "jobs/j1/assets/graphic-scene-02.png"  # other scene ok


def test_graphic_prompt_omits_style_dna_and_prioritizes_scene_content(tmp_path):
    """User feedback 2026-07-09: long-form ChatGPT graphic cards had collapsed
    into one repeated wellness-magazine template. Graphic image prompts must be
    content-first and must not inject channel style-DNA palette/mood."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")
    captured_prompts: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured_prompts.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    asyncio.run(run_graphic_images_stage(job_dir, None, _capture))

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "CONTENT-FIRST ART DIRECTION" in prompt
    assert "Use ONLY this brand palette" not in prompt
    assert "Brand style" not in prompt
    assert "#A47A3F" not in prompt
    assert "#F6F1E8" not in prompt
    assert "#2F6B57" not in prompt
    assert "wellness-magazine" not in prompt
    # bug-542: the anti-template rule stays, but must NOT name the colours it
    # forbids (a negative instruction still conditions the model on them).
    assert "Do NOT force a recurring" in prompt
    assert "brand palette" in prompt
    assert "cream" not in prompt.lower() and "green" not in prompt.lower()
    assert "scene's specific idea" in prompt


def test_graphic_prompt_sent_to_generator_is_premium_mobile_clear_and_single_idea(tmp_path):
    scenes = [{
        "id": "scene-01",
        "layout": "steps",
        "graphic": {"needed": True, "prompt": "A real kitchen counter with potatoes"},
        "layout_payload": {
            "title": "Tres preguntas",
            "body": "Elige una respuesta clara",
            "bullets": ["¿Qué añades?", "¿Cuándo lo haces?", "¿Cuánto usas?"],
            "cta": "",
        },
    }]
    job_dir = _make_job(tmp_path, scenes)
    captured: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    asyncio.run(run_graphic_images_stage(job_dir, None, _capture))

    final_prompt = captured[0]
    assert "PREMIUM EDITORIAL STANDARD" in final_prompt
    assert "one visual idea" in final_prompt.lower()
    assert "aimed at adults 45+" in final_prompt
    assert "phone-sized preview" in final_prompt
    assert "no more than 24 rendered words" in final_prompt
    assert 'Closing line: "Elige una respuesta clara".' in final_prompt


def test_steps_content_does_not_repeat_bullets_as_a_closing_line():
    questions = ["¿Qué añades?", "¿Cuándo lo haces?", "¿Cuánto usas?"]
    repeated_body = "QUÉ AÑADES, CUÁNDO LO HACES; CUÁNTO USAS"

    lines = _content_lines("steps", "Tres preguntas", repeated_body, questions, "")
    rendered = " ".join(lines)

    assert rendered.count("¿Qué añades?") == 1
    assert "Closing line" not in rendered


def test_content_deduplicates_body_and_cta_against_any_visible_field():
    lines = _content_lines(
        "cta",
        "Cuida tu rutina",
        "CUIDA TU RUTINA",
        ["Paso sencillo"],
        "Paso sencillo",
    )
    rendered = " ".join(lines)

    assert rendered.count("Cuida tu rutina") == 1
    assert "Supporting line" not in rendered
    assert "Call-to-action button" not in rendered


def test_distinct_cta_is_rendered():
    rendered = " ".join(
        _content_lines("cta", "Cuida tu rutina", "Paso sencillo", [], "Suscríbete")
    )

    assert 'Call-to-action button labelled: "Suscríbete".' in rendered


def test_cta_channel_name_is_included_inside_24_word_budget(tmp_path):
    scene = {
        "id": "scene-01",
        "layout": "cta",
        "graphic": {"needed": True, "prompt": "A calm editorial closing"},
        "layout_payload": {
            "title": "Cuida hoy tu rutina diaria",
            "body": "Consejos sencillos para sentirte mejor",
            "bullets": [
                "Paso fácil hoy",
                "Cambio real mañana",
                "Rutina sin prisa",
                "Bienestar cada día",
            ],
            "cta": "Suscríbete ahora",
        },
    }
    job_dir = _make_job(tmp_path, [scene])
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text("channel:\n  name: Vida Plena 45 Plus\n", encoding="utf-8")
    captured: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    asyncio.run(run_graphic_images_stage(job_dir, channel_path, _capture))

    prompt = captured[0]
    assert '"Suscríbete ahora"' in prompt
    assert '"Vida Plena 45 Plus"' in prompt
    assert "Consejos sencillos" not in prompt
    assert "Paso fácil hoy" in prompt


def test_cta_too_long_to_fit_with_channel_name_skips_unreadable_graphic(tmp_path):
    long_cta = (
        "Suscríbete ahora para recibir cada semana más consejos prácticos claros "
        "sencillos realistas seguros útiles cercanos y fáciles de aplicar hoy siempre"
    )
    scene = {
        "id": "scene-01",
        "layout": "cta",
        "graphic": {"needed": True, "prompt": "A calm editorial closing"},
        "layout_payload": {"title": "Sigue aprendiendo", "body": "", "bullets": [], "cta": long_cta},
    }
    job_dir = _make_job(tmp_path, [scene])
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text("channel:\n  name: Vida Plena 45 Plus\n", encoding="utf-8")
    captured: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured.append(prompt)
        raise AssertionError("over-budget CTA must not reach image generation")

    out = asyncio.run(run_graphic_images_stage(job_dir, channel_path, _capture))

    assert captured == []
    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert "image_ref" not in graphic


def test_structured_payload_with_empty_body_does_not_reintroduce_long_caption(tmp_path):
    scene = {
        "id": "scene-01",
        "layout": "steps",
        "caption": "Una explicación oral larga que no debe convertirse en letra pequeña.",
        "graphic": {"needed": True, "prompt": "A practical kitchen action"},
        "layout_payload": {
            "title": "Tres pasos",
            "body": "",
            "bullets": ["Añade", "Espera", "Sirve"],
            "cta": "",
        },
    }
    job_dir = _make_job(tmp_path, [scene])
    captured: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    asyncio.run(run_graphic_images_stage(job_dir, None, _capture))

    assert "explicación oral larga" not in captured[0]
    assert all(word in captured[0] for word in ("Añade", "Espera", "Sirve"))


def test_graphic_metadata_records_style_dna_disabled(tmp_path):
    """The prompt hash is still persisted for cache safety, but style DNA is no
    longer part of long-form graphic generation."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")
    written: list[str] = []

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))

    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert isinstance(graphic.get("prompt_hash"), str) and len(graphic["prompt_hash"]) > 0
    assert graphic["style_dna_disabled"] is True
    assert "effective_palette" not in graphic
    assert "resolved_accent_color" not in graphic


def test_topic_accent_change_does_not_regenerate_content_first_graphic(tmp_path):
    """Changing SEO topic colour used to invalidate every graphic via style DNA.
    Long-form graphics are now content-first, so colour/DNA drift must not force
    regeneration when scene content is unchanged."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")
    written: list[str] = []
    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))
    assert len(written) == 1
    first_doc = json.loads((job_dir / "json" / "scenes.json").read_text())
    first_hash = first_doc["scenes"][0]["graphic"]["prompt_hash"]

    # Simulate a re-run of the stage after seo.topic_accent_color changed --
    # job.json must allow re-entering the stage, so reset its status.
    job_state = json.loads((job_dir / "job.json").read_text())
    for s in job_state["stages"]:
        if s["name"] == "graphic_images":
            s["status"] = "pending"
    (job_dir / "job.json").write_text(json.dumps(job_state))
    (job_dir / "json" / "seo.json").write_text(json.dumps({"topic_accent_color": "#2F6B57"}))

    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))

    assert len(written) == 1  # reused; colour/DNA is not part of the prompt now
    second_doc = json.loads((job_dir / "json" / "scenes.json").read_text())
    second_graphic = second_doc["scenes"][0]["graphic"]
    second_hash = second_graphic["prompt_hash"]
    assert second_hash == first_hash
    assert second_graphic["style_dna_disabled"] is True


def test_missing_prompt_hash_forces_regeneration(tmp_path):
    """bug-468: a real incident (2026-07-03) on a production job -- a scene
    whose cached PNG predates the prompt_hash feature (no stored hash at all,
    not just a mismatched one) was silently REUSED with its old pixels while
    scenes.json's resolved_accent/background/text_color fields were
    overwritten to reflect the CURRENT run's colours -- metadata claimed a
    colour treatment the actual image never got. This stage only re-enters a
    job when a human deliberately resets it (job.json otherwise skips
    "completed" stages), so a missing hash must be treated as stale too, not
    as an exemption from regeneration."""
    scenes = [{
        "id": "scene-01", "layout": "checklist", "caption": "Reduce el azucar",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes, topic_accent_color="#A47A3F")

    # Simulate a pre-bug-465 cached PNG: file exists on disk, but scenes.json
    # has no prompt_hash for it at all (the field never existed before bug-465).
    out_path = job_dir / "assets" / "graphic-scene-01.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\x89PNG\r\n" + b"OLD-PRE-BUG-465-PIXELS")
    scenes_doc = json.loads((job_dir / "json" / "scenes.json").read_text())
    scenes_doc["scenes"][0]["graphic"] = {"needed": True, "prompt": "x"}  # no prompt_hash key
    (job_dir / "json" / "scenes.json").write_text(json.dumps(scenes_doc))

    written: list[str] = []
    asyncio.run(run_graphic_images_stage(job_dir, None, _fake_image_fn(written)))

    assert written == [str(out_path)]  # regenerated, not silently reused
    graphic = json.loads((job_dir / "json" / "scenes.json").read_text())["scenes"][0]["graphic"]
    assert isinstance(graphic.get("prompt_hash"), str) and len(graphic["prompt_hash"]) > 0


def test_late_written_graphic_is_adopted_after_client_failure(tmp_path, monkeypatch):
    """Codex 20260704-130051 (production scene-27): the browser-worker often
    finishes and writes the PNG minutes AFTER the client-side request already
    failed (observed lag 123s). The end-of-stage sweep must adopt that
    late-landed file instead of abandoning a finished card."""
    monkeypatch.setenv("GRAPHIC_LATE_RECOVERY_WINDOW_SEC", "5")
    scenes = [{
        "id": "scene-01", "layout": "recipe_snapshot", "caption": "Plato equilibrado",
        "graphic": {"needed": True, "prompt": "x"},
    }]
    job_dir = _make_job(tmp_path, scenes)
    out_path = job_dir / "assets" / "graphic-scene-01.png"

    async def _fails_but_writes_late(*, prompt, project_name, out_path):
        # Simulate the real browser-worker: the HTTP request errors client-side,
        # but the server-side generation completes and writes the file anyway.
        async def _late_write():
            await asyncio.sleep(0.3)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x89PNG\r\n" + b"LATE-SERVER-WRITE")
        asyncio.get_running_loop().create_task(_late_write())
        raise RuntimeError("browser-worker chatgpt/image request failed: ")

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _fails_but_writes_late))

    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert graphic.get("image_ref") == "jobs/j1/assets/graphic-scene-01.png"
    assert not graphic.get("failed")
    assert out_path.exists() and out_path.stat().st_size > 0


def test_unrecovered_failure_stamps_marker_and_removes_stale_orphan(tmp_path):
    """When generation truly fails (nothing lands within the recovery window):
    (a) a pre-existing STALE png (the reason regeneration was attempted) must
    be deleted so audits never find an orphan file unreferenced by metadata;
    (b) the scene must carry an explicit graphic.failed marker so visual
    review can flag the lost card instead of a silent downgrade."""
    scenes = [{
        "id": "scene-01", "layout": "recipe_snapshot", "caption": "Plato equilibrado",
        "graphic": {"needed": True, "prompt": "x"},  # no prompt_hash -> stale
    }]
    job_dir = _make_job(tmp_path, scenes)
    out_path = job_dir / "assets" / "graphic-scene-01.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\x89PNG\r\n" + b"STALE-OLD-PROMPT-PIXELS")

    async def _always_fails(*, prompt, project_name, out_path):
        raise RuntimeError("browser-worker chatgpt/image request failed: ")

    out = asyncio.run(run_graphic_images_stage(job_dir, None, _always_fails))

    graphic = json.loads(out.read_text())["scenes"][0]["graphic"]
    assert graphic.get("failed") is True
    assert "failed" in str(graphic.get("error"))
    assert not graphic.get("image_ref")
    assert not out_path.exists()  # stale orphan removed


# ── bug-542: no framework-owned named colours in ANY long graphic prompt ────

_FORBIDDEN_COLOR_WORDS = (
    "cream", "green", "red", "navy", "orange", "pink",
    "white", "black", "beige", "yellow", "blue",
)

# Neutral scene content per layout: contains NO colour word itself, so any
# colour token in the final prompt is framework-owned leakage.
_NEUTRAL_LAYOUT_SCENES = {
    "checklist": {"caption": "Revisa la etiqueta antes de comprar"},
    "comparison": {"caption": "Cena muy pesada frente a cena demasiado escasa"},
    "cta": {"caption": "Suscribete al canal"},
    "do_dont": {"caption": "Camina despues de comer"},
    "evidence_nugget": {"caption": "Ocho de cada diez adultos lo ignoran"},
    "hook": {"caption": "La sal se esconde en la cena"},
    "myth": {"caption": "El pan integral siempre adelgaza"},
    "plate_map": {"caption": "Reparte el plato en tres partes"},
    "quote": {"caption": "Comer despacio cambia la digestion"},
    "quote_portrait": {"caption": "La constancia importa mas que la perfeccion"},
    "recipe_snapshot": {"caption": "Avena con fruta en cinco minutos"},
    "stat": {"caption": "Treinta por ciento menos sodio"},
    "steps": {"caption": "Tres pasos para dormir mejor"},
    "warning": {"caption": "Cuidado con las salsas preparadas"},
}


def _capture_layout_prompt(tmp_path, layout: str) -> str:
    scene = {
        "id": "scene-01", "layout": layout,
        "caption": _NEUTRAL_LAYOUT_SCENES[layout]["caption"],
        "graphic": {"needed": True, "prompt": "x"},
    }
    job_dir = _make_job(tmp_path / layout, [scene], topic_accent_color="#A47A3F")
    captured: list[str] = []

    async def _capture(*, prompt: str, project_name: str, out_path: str):
        captured.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"\x89PNG\r\n")
        return {"src": "https://chatgpt/img", "bytes": 6}

    asyncio.run(run_graphic_images_stage(job_dir, None, _capture))
    assert captured, f"{layout}: no prompt captured"
    return captured[0]


def test_no_framework_owned_color_word_in_any_layout_prompt(tmp_path):
    """R1/U1: with neutral scene content, the FINAL prompt sent to image_fn must
    carry no source-owned colour name for ANY supported long graphic layout."""
    import re

    from video_agent.visual.spans import GRAPHIC_LAYOUTS

    for layout in sorted(GRAPHIC_LAYOUTS):
        prompt = _capture_layout_prompt(tmp_path, layout)
        low = prompt.lower()
        for word in _FORBIDDEN_COLOR_WORDS:
            assert not re.search(rf"\b{word}\b", low), f"{layout}: leaked '{word}'"


def test_no_style_dna_or_topic_accent_hex_in_any_layout_prompt(tmp_path):
    """R3/U1-2: Style DNA stays disabled — no channel/SEO hex may appear."""
    import re

    from video_agent.visual.spans import GRAPHIC_LAYOUTS

    for layout in sorted(GRAPHIC_LAYOUTS):
        prompt = _capture_layout_prompt(tmp_path, layout)
        assert not re.search(r"#[0-9A-Fa-f]{6}", prompt), f"{layout}: hex leaked"
        assert "#A47A3F" not in prompt  # SEO topic accent
        assert "CONTENT-FIRST ART DIRECTION" in prompt


def test_content_first_direction_and_scene_text_survive_for_every_layout(tmp_path):
    """R2/R5: removing colour words must not strip the content-first drivers."""
    from video_agent.visual.spans import GRAPHIC_LAYOUTS

    for layout in sorted(GRAPHIC_LAYOUTS):
        prompt = _capture_layout_prompt(tmp_path, layout)
        assert "scene's specific idea" in prompt, layout
        # The scene's own words still reach the model.
        first_word = _NEUTRAL_LAYOUT_SCENES[layout]["caption"].split()[0]
        assert first_word.lower() in prompt.lower(), layout
