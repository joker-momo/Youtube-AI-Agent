"""Shorts Autopilot v5 — Phase 3/4: prompts, source map, seo, QA, build_short."""
from __future__ import annotations

import json
from pathlib import Path


def _long_job(tmp_path: Path) -> Path:
    job = tmp_path / "long-job"
    job.mkdir()
    scenes = [
        {"id": "scene-09", "duration_sec": 16.0, "narration": "Empieza por marcar una hora de cierre.",
         "visual_prompt": "woman at night, vertical", "layout": "short_tip", "audio_offset_sec": 183.0},
    ]
    (job / "scenes.json").write_text(json.dumps({"scenes": scenes, "total_duration_sec": 600}), encoding="utf-8")
    (job / "script.json").write_text(json.dumps({"hook": "h", "sections": [], "narration": "n", "cta": "c"}), encoding="utf-8")
    (job / "seo.json").write_text(json.dumps({"title": "Dormir mejor después de los 45"}), encoding="utf-8")
    (job / "whisper_timestamps.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
    (job / "video.mp4").write_bytes(b"x")
    return job


def _cfg():
    return {
        "channel": {"id": "vida-plena-45"},
        "shorts": {
            "autopilot": {"max_regeneration_attempts": 2},
            "duration": {"min_sec": 20, "target_max_sec": 60},
            "tts": {"provider": "kokoro", "voice_id": "ef_dora", "speed": 1.07},
            "funnel": {"default_cta_without_url": "Vídeo completo en el canal.", "cta_max_words": 8},
        },
    }


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

_GOOD_SCRIPT = {
    "short_id": "short-01", "source_long_job_id": "long-job", "short_format": "pain_to_tip",
    "target_duration_sec": 32, "hook": "¿Duermes pero te levantas cansado?",
    "narration": "¿Duermes pero te levantas cansado? Marca una hora de cierre y apaga la pantalla.\nNotarás la diferencia.",
    "beats": [
        {"time_sec": 0, "narration": "¿Duermes pero te levantas cansado?", "purpose": "hook"},
        {"time_sec": 2, "narration": "Marca una hora de cierre.", "purpose": "tip"},
        {"time_sec": 5, "narration": "Apaga la pantalla.", "purpose": "tip"},
        {"time_sec": 8, "narration": "Notarás la diferencia.", "purpose": "tip"},
        {"time_sec": 11, "narration": "Vídeo completo en el canal.", "purpose": "cta"}
    ], 
    "cta": "Vídeo completo en el canal.", "qa": {"verdict": "PENDING_SHORTS_QA"},
}
_GOOD_SCENES = {
    "channel_id": "vida-plena-45", "short_id": "short-01", "total_duration_sec": 21.0,
    "scenes": [
        {"id": "s1", "duration_sec": 2.5, "on_screen_text": "MENTE ENCENDIDA", "caption": "c", "layout": "short_hook", "visual_prompt": "v vertical", "narration": "¿Duermes pero te levantas cansado?"},
        {"id": "s2", "duration_sec": 4.2, "on_screen_text": "HORA DE CIERRE", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Marca una hora de cierre."},
        {"id": "s3", "duration_sec": 4.2, "on_screen_text": "APAGA PANTALLA", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Apaga la pantalla."},
        {"id": "s4", "duration_sec": 4.2, "on_screen_text": "RESPIRA DESPACIO", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Respira despacio."},
        {"id": "s5", "duration_sec": 3.5, "on_screen_text": "BAJA EL RITMO", "caption": "c", "layout": "short_tip", "visual_prompt": "v vertical", "narration": "Baja el ritmo antes de dormir."},
        {"id": "s6", "duration_sec": 2.4, "on_screen_text": "GUARDA ESTA IDEA", "caption": "c", "layout": "short_cta", "visual_prompt": "v vertical", "narration": "Guarda esta idea."},
    ],
    "qa": {"verdict": "PENDING_SHORTS_QA"},
}


def _llm_fn_factory(script=_GOOD_SCRIPT, scenes=_GOOD_SCENES):
    def fn(kind, prompt):
        if kind == "script":
            return json.dumps(script)
        if kind == "scenes":
            return json.dumps(scenes)
        if kind == "seo":
            return json.dumps({"title": "Dormir mejor 45+", "description": "d", "hashtags": ["#shorts"],
                               "pinned_comment": "Mira el vídeo largo"})
        return "{}"
    return fn


def _stub_io(calls):
    def background_fn(short_dir, short_scenes, channel_config, on_scene_resolved=None):
        calls.append("background")
        scenes = short_scenes.get("scenes") or []
        for i, sc in enumerate(scenes):
            sc.setdefault("asset_refs", {})["background"] = f"jobs/x/assets/{sc['id']}.mp4"
            if str(sc.get("layout") or "").startswith("graphic_"):
                sc["generated_image_source_layout"] = sc["layout"]
                sc["layout"] = "short_tip"
                sc["background_mode"] = "generated_image"
            if on_scene_resolved:
                on_scene_resolved({"index": i, "total": len(scenes), "scene_id": sc["id"], "phase": "resolved", "background_source": "Pexels video"})
    def tts_fn(short_dir, short_scenes, channel_config):
        calls.append("tts"); (short_dir / "audio").mkdir(parents=True, exist_ok=True)
        (short_dir / "audio" / "short_narration.wav").write_bytes(b"w"); return short_dir / "audio" / "short_narration.wav"
    def mix_fn(short_dir, narration_wav, music_track, channel_config, duration_sec):
        calls.append("mix"); (short_dir / "audio" / "short_mix.m4a").write_bytes(b"m"); return short_dir / "audio" / "short_mix.m4a"
    def render_fn(short_dir, channel_config):
        calls.append("render"); (short_dir / "outputs").mkdir(parents=True, exist_ok=True)
        out = short_dir / "outputs" / "short.mp4"
        out.write_bytes(b"v"); return out
    return dict(background_fn=background_fn, tts_fn=tts_fn, mix_fn=mix_fn, render_fn=render_fn)


def _three_graphic_scenes():
    """Mirrors the failing short-02_idea-02 candidate: a checklist Short with
    3 graphics (setup checklist + label callout + comparison)."""
    return [
        {"id": "s01", "duration_sec": 3.0, "layout": "short_hook", "on_screen_text": "MARRON NO BASTA", "caption": "c", "visual_prompt": "manos sostienen pan integral en el súper, vertical", "narration": "El pan marrón no es integral."},
        {"id": "s02", "duration_sec": 3.5, "layout": "short_tip", "on_screen_text": "REVISA RAPIDO", "caption": "c", "visual_prompt": "carrito de la compra en pasillo de panadería, vertical", "narration": "Haz esta revisión rápida."},
        {"id": "s03", "duration_sec": 4.0, "layout": "graphic_checklist", "on_screen_text": "TRES PASOS", "caption": "c", "visual_prompt": "checklist", "narration": "Tres comprobaciones rápidas.", "layout_payload": {"title": "TRES PASOS", "items": ["Color no basta", "Primer ingrediente", "Compara fibra"]}},
        {"id": "s04", "duration_sec": 4.5, "layout": "graphic_label_callout", "on_screen_text": "PRIMER INGREDIENTE", "caption": "c", "visual_prompt": "vertical nutrition label close-up", "narration": "Busca harina integral al principio.", "layout_payload": {"title": "PRIMER INGREDIENTE", "productLabel": "Pan integral", "callouts": [{"label": "Harina", "value": "integral"}, {"label": "Fibra", "value": "6 g"}]}},
        {"id": "s05", "duration_sec": 3.5, "layout": "short_tip", "on_screen_text": "EN EL SUPER", "caption": "c", "visual_prompt": "persona comparando dos panes en el supermercado, vertical", "narration": "Compáralo en el súper."},
        {"id": "s06", "duration_sec": 4.5, "layout": "graphic_comparison", "on_screen_text": "FIBRA Y AZUCAR", "caption": "c", "visual_prompt": "vertical two labels", "narration": "Compara fibra y azúcar.", "layout_payload": {"title": "EN EL SÚPER", "left": {"heading": "MEJOR", "text": "Más fibra"}, "right": {"heading": "CUIDADO", "text": "Más azúcar"}}},
        {"id": "s07", "duration_sec": 2.5, "layout": "short_cta", "on_screen_text": "GUARDA ESTA LISTA", "caption": "c", "visual_prompt": "pan en cesta de la compra, vertical", "narration": "Guarda esta lista."},
    ]


def _scene_qa_scores() -> dict:
    return {
        "audience_fit_45_plus": 10, "hook_strength": 10, "visual_specificity": 10,
        "clarity": 10, "retention_pacing": 9, "natural_spanish": 10, "saveability": 10,
    }



__all__ = [
    "json",
    "Path",
    "_long_job",
    "_cfg",
    "_GOOD_SCRIPT",
    "_GOOD_SCENES",
    "_llm_fn_factory",
    "_stub_io",
    "_three_graphic_scenes",
    "_scene_qa_scores",
]
