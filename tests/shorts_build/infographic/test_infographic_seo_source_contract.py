from __future__ import annotations

from pathlib import Path

SOURCE_IDEA = {
    "idea_id": "idea-09",
    "format": "warning_list",
    "title": "6 errores que convierten una cena aparentemente saludable en una suma de sal",
    "viewer_pain": "Una cena saludable puede acumular demasiada sal",
    "practical_payoff": "Detectar seis combinaciones con demasiada sal",
    "key_points": [{"point": f"Error {index}"} for index in range(1, 7)],
}

POSTER_PLAN = {
    "title": "6 errores con la sal",
    "hook_line": "Cena saludable, demasiada sal",
    "cta": "Revisa etiquetas y combina mejor",
    "items": [{"label": f"Error {index}"} for index in range(1, 7)],
}


def test_infographic_seo_preserves_selected_source_idea_contract(monkeypatch, tmp_path: Path) -> None:
    from video_agent.shorts.infographic import seo as infographic_seo

    captured: dict = {}

    def fake_build_short_seo(
        long_job_dir, short_id, short_plan, short_script, channel_config, llm_fn, **kwargs
    ):
        captured.update(short_plan=short_plan, short_script=short_script)
        return {"short_id": short_id, "title": "¡6 errores con sal en tu cena!"}

    monkeypatch.setattr(infographic_seo, "build_short_seo", fake_build_short_seo)
    infographic_seo.build_infographic_seo(
        tmp_path,
        "short-idea-09",
        POSTER_PLAN,
        {},
        lambda prompt: "{}",
        source_idea=SOURCE_IDEA,
    )

    assert captured["short_plan"]["idea_id"] == "idea-09"
    assert captured["short_plan"]["format"] == "warning_list"
    assert captured["short_plan"]["title"] == SOURCE_IDEA["title"]
    assert captured["short_plan"]["viewer_pain"] == SOURCE_IDEA["viewer_pain"]
    assert captured["short_plan"]["practical_payoff"] == SOURCE_IDEA["practical_payoff"]
    contract = captured["short_script"]["idea_contract"]
    assert contract["original_count"] == 6
    assert contract["must_preserve_count"] is True


def test_real_infographic_orchestrator_passes_source_idea_to_seo(
    monkeypatch, tmp_path: Path
) -> None:
    from video_agent.shorts.infographic import build

    short_dir = tmp_path / "parent" / "shorts" / "short-idea-09"
    captured: dict = {}

    monkeypatch.setattr(build, "build_poster_plan", lambda cfg, source, llm: POSTER_PLAN)

    async def fake_generate_poster(short_dir, plan, image_fn, channel_config):
        poster = Path(short_dir) / "assets" / "poster.png"
        poster.parent.mkdir(parents=True, exist_ok=True)
        poster.write_bytes(b"poster")
        return poster

    monkeypatch.setattr(build, "generate_poster", fake_generate_poster)

    def fake_music(short_dir, track, cfg, duration):
        path = Path(short_dir) / "audio" / "music.m4a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"music")
        return path

    def fake_seo(long_job_dir, short_id, plan, channel_config, llm_fn, **kwargs):
        captured.update(kwargs)
        return {"short_id": short_id, "title": "¡6 errores con sal en tu cena!"}

    def fake_render(short_dir, props):
        out = Path(short_dir) / "outputs" / "short.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"video")
        return out

    monkeypatch.setattr(build, "build_infographic_seo", fake_seo)
    build.run_infographic_short(
        short_dir,
        {"shorts": {"infographic": {"duration_sec": 15}}},
        SOURCE_IDEA,
        image_fn=lambda **kwargs: None,
        llm_fn=lambda prompt: "{}",
        render_fn=fake_render,
        music_fn=fake_music,
    )

    assert captured["source_idea"] == SOURCE_IDEA

