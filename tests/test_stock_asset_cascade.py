"""Tests for the 3-tier visual asset cascade (video -> photo -> AI-gen).

Root cause of weak hook visuals: a stock video search for an action-specific
prompt ("turning a bread package to read the label") returns no clip that
actually depicts the action, so the service silently accepted a generic
resolution-only match ("slicing bread"). The cascade now descends to a Pexels
photo, then to AI image generation, and only as an anti-blank last resort
accepts a weak stock match (flagged weak_match=True).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from video_agent.assets.service import StockAssetService


class _FakeStock:
    def __init__(self, by_provider):
        self.by_provider = by_provider

    def search(self, provider, query, filters, exclude_ids=None):
        return {"provider": provider}

    def normalize(self, provider, response):
        return list(self.by_provider.get(provider, []))


def _cand(asset_id, provider, tags, media_type="video"):
    return {
        "provider": provider,
        "provider_asset_id": asset_id,
        "asset_id": asset_id,
        "media_type": media_type,
        "width": 1920,
        "height": 1080,
        "tags": list(tags),
    }


def _service(by_provider, *, image_gen_fn=None):
    svc = StockAssetService(
        visual_config={"providers": ["pexels_video"], "strategy": "auto"},
        stock_client=_FakeStock(by_provider),
        download_client=SimpleNamespace(),
        image_gen_fn=image_gen_fn,
    )
    import uuid
    svc.core.cache = SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None)
    svc.core.library = SimpleNamespace(
        root=Path(f"/tmp/stub_lib_{uuid.uuid4().hex}"),
        record_usage=lambda *a, **k: None,
        get_by_provider_id=lambda *a, **k: None,
        is_file_valid=lambda a: True,
        search_by_query=lambda *a, **k: [],
    )
    svc.core._ensure_asset = lambda candidate, query: {  # type: ignore[assignment]
        **candidate,
        "provider": candidate["provider"],
        "asset_id": candidate["provider_asset_id"],
    }
    return svc


# A bread-label scene whose action ("turning package to read label") stock video
# cannot satisfy. Query terms include bread / package / label / supermarket.
def _scene():
    return {
        "id": "s01",
        "motion": "push_in",
        "duration_sec": 3,
        "visual_prompt": "supermarket bread package label, hand turning package to read the back label",
    }


_STRICT_TAGS = ["bread", "package", "label", "supermarket", "read", "turning"]
_WEAK_TAGS = ["airplane", "sky", "runway"]


def test_tier1_video_strict_match_is_used():
    svc = _service({"pexels_video": [_cand("vid-ok", "pexels_video", _STRICT_TAGS)]})
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_id"] == "vid-ok"
    assert asset["asset_selection"].get("weak_match") is False


def test_descends_to_photo_when_video_has_no_strict_match():
    svc = _service({
        "pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)],
        "pexels": [_cand("photo-ok", "pexels", _STRICT_TAGS, media_type="photo")],
    })
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_id"] == "photo-ok"
    assert asset["media_type"] == "photo"
    assert asset["asset_selection"].get("weak_match") is False


def test_descends_to_ai_gen_when_no_strict_stock():
    calls = {"n": 0}

    def fake_gen(prompt, out_path):
        calls["n"] += 1
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"fake-image" * 200)

    svc = _service(
        {
            "pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)],
            "pexels": [_cand("photo-weak", "pexels", _WEAK_TAGS, media_type="photo")],
        },
        image_gen_fn=fake_gen,
    )
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert calls["n"] == 1
    assert asset["asset_selection"].get("source") == "ai_generated"
    assert asset.get("media_type") == "image"


def test_skip_ai_fallback_flag_defers_chatgpt_for_video_covered_scene():
    # Lazy AI policy: a scene whose span is routed to native-video QA carries
    # _skip_ai_fallback=True -> the cascade must NOT call ChatGPT image gen; it
    # degrades to a graphic_fallback placeholder unless visual_local_qa later
    # rejects the span video and fallback regen explicitly re-enables AI.
    calls = {"n": 0}

    def fake_gen(prompt, out_path):
        calls["n"] += 1

    svc = _service(
        {
            "pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)],
            "pexels": [_cand("photo-weak", "pexels", _WEAK_TAGS, media_type="photo")],
        },
        image_gen_fn=fake_gen,
    )
    scene = {**_scene(), "_skip_ai_fallback": True}
    asset = svc.get_scene_asset(scene, channel_id="ch", job_id="job")
    assert calls["n"] == 0  # ChatGPT NOT invoked
    assert asset is not None
    assert asset["asset_tier"] == "graphic_fallback"


def test_ai_image_preferred_skips_stock_search_and_goes_directly_to_chatgpt(tmp_path):
    calls = {"image": 0}
    svc = _service({"pexels_video": [_cand("vid-ok", "pexels_video", _STRICT_TAGS)]})
    svc.core.library.root = tmp_path / "asset_library"

    def fake_gen(prompt, out_path):
        calls["image"] += 1
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x" * 2048)

    svc.image_gen_fn = fake_gen
    scene = {**_scene(), "asset_strategy": "ai_image_preferred"}

    asset = svc.get_scene_asset(scene, channel_id="ch", job_id="job")

    assert asset is not None
    assert asset["provider"] == "ai_generated"
    assert calls["image"] == 1


def test_weak_stock_fallback_when_no_ai_and_no_strict():
    # No photo provider data, no image_gen -> key scene should graphic_fallback
    svc = _service({"pexels_video": [_cand("vid-weak", "pexels_video", _WEAK_TAGS)]})
    asset = svc.get_scene_asset(_scene(), channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_tier"] == "graphic_fallback"
    assert asset["asset_selection"]["asset_match_status"] == "graphic_fallback"

def test_prepare_assets_regression_weak_match_blocked_on_hook_fallback():
    # prompt asks hand turning bread package/back label,
    # Pexels returns slicing bread,
    # expected weak_match rejected for short_hook,
    # fallback to ai_generated or graphic_fallback.
    svc = _service({"pexels_video": [_cand("vid-weak", "pexels_video", ["slice", "cutting"])]})
    key_scene = {
        "id": "s02",
        "motion": "push_in",
        "duration_sec": 3,
        "visual_prompt": "hand turning bread package to show back label",
        "layout": "short_hook"
    }
    # With no AI enabled in mock, it should fallback to graphic_fallback
    asset = svc.get_scene_asset(key_scene, channel_id="ch", job_id="job")
    assert asset is not None
    assert asset["asset_tier"] == "graphic_fallback"

def test_prepare_assets_regression_weak_match_contradictory_blocked():
    # Non-key scene with contradictory match should skip the contradictory weak candidate.
    # Since there are no other candidates, it should block (return None).
    svc = _service({"pexels_video": [_cand("vid-weak", "pexels_video", ["sleep", "bed"])]})
    contradictory_scene = {
        "id": "s03",
        "motion": "pan_left",
        "duration_sec": 3,
        "visual_prompt": "a beautiful supermarket store",
        "layout": "default"
    }
    asset = svc.get_scene_asset(contradictory_scene, channel_id="ch", job_id="job")
    assert asset is None


def test_standard_layout_retains_placeholder_on_fallback(tmp_path):
    from video_agent.stages.assets import prepare_assets

    scene_doc = {
        "total_duration_sec": 3,
        "scenes": [
            {
                "id": "s01",
                "layout": "short_hook",
                "on_screen_text": "GIRA EL PAQUETE",
                "visual_prompt": "some prompt",
                "asset_refs": {}
            }
        ]
    }

    palette = {
        "palette": {
            "background": "#F6F1E8",
            "primary": "#2F6B57",
            "secondary": "#D98C5F",
            "accent": "#F5C24B",
            "text": "#26332F"
        }
    }

    class GraphicFallbackStockClient:
        def search(self, provider, query, filters):
            return {}
        def normalize(self, provider, response):
            return []

    job_dir = tmp_path / "jobs" / "job-std-fallback"

    manifest = prepare_assets(
        job_dir=job_dir,
        style_dna=palette,
        scene_doc=scene_doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=GraphicFallbackStockClient(),
        image_gen_fn=lambda p, o: None
    )

    scene = manifest["scenes"][0]
    assert scene["background"] != ""
    assert scene["source"] == "generated_placeholder"
    assert "graphic_fallback" in scene["provider"]
    assert scene_doc["scenes"][0]["asset_refs"]["background"] != ""


def test_graphic_layout_generates_chatgpt_image_with_full_payload(tmp_path):
    from PIL import Image

    from video_agent.stages.assets import prepare_assets

    gen_calls = []

    def _img_gen(prompt, out_path):
        gen_calls.append((prompt, Path(out_path)))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1920), (30, 50, 45)).save(out_path)

    scene_doc = {
        "total_duration_sec": 3,
        "scenes": [
            {
                "id": "s01",
                "layout": "graphic_label_callout",
                "on_screen_text": "GIRA EL PAQUETE",
                "visual_prompt": "Spanish supermarket bread label education card",
                "layout_payload": {
                    "title": "MIRA LA ETIQUETA",
                    "productLabel": "Pan integral",
                    "callouts": [
                        {"label": "Ingrediente 1", "value": "Harina integral"},
                        {"label": "Fibra", "value": "3 g o más"},
                    ],
                },
                "asset_refs": {}
            }
        ]
    }

    palette = {
        "palette": {
            "background": "#F6F1E8",
            "primary": "#2F6B57",
            "secondary": "#D98C5F",
            "accent": "#F5C24B",
            "text": "#26332F"
        }
    }

    class GraphicFallbackStockClient:
        def search(self, provider, query, filters):
            return {}
        def normalize(self, provider, response):
            return []

    job_dir = tmp_path / "jobs" / "job-graph-fallback"

    manifest = prepare_assets(
        job_dir=job_dir,
        style_dna=palette,
        scene_doc=scene_doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=GraphicFallbackStockClient(),
        image_gen_fn=_img_gen,
    )

    scene = manifest["scenes"][0]
    assert len(gen_calls) == 1
    prompt = gen_calls[0][0]
    assert "MIRA LA ETIQUETA" in prompt
    assert "Harina integral" in prompt
    assert "Fibra" in prompt
    assert scene_doc["scenes"][0]["asset_refs"]["background"] != ""
    assert scene_doc["scenes"][0]["layout"] == "short_tip"
    assert scene["provider"] == "ai_generated"
    assert scene["background_source"] == "ChatGPT infographic"


def test_graphic_comparison_image_prompt_is_structured_and_exact():
    from video_agent.assets.service import build_scene_image_prompt

    prompt = build_scene_image_prompt(
        {
            "id": "s04",
            "layout": "graphic_comparison",
            "on_screen_text": "DESAYUNO: 1 O 2",
            "visual_prompt": "Two realistic Spanish breakfast plates: left has one bread slice, right has two bread slices.",
            "narration": "En desayuno, una o dos rebanadas pueden encajar.",
            "layout_payload": {
                "title": "DESAYUNO: 1 O 2",
                "left": {"heading": "1 REBANADA", "text": "Mira el tamaño"},
                "right": {"heading": "2 REBANADAS", "text": "Mira el acompañamiento"},
                "footer": "Depende del plato.",
            },
        },
        "breakfast bread portion",
    )

    assert "graphic_comparison" in prompt
    assert "side-by-side" in prompt
    assert "LEFT PANEL" in prompt
    assert "RIGHT PANEL" in prompt
    assert "Main headline to include exactly: DESAYUNO: 1 O 2" in prompt
    assert "1 REBANADA" in prompt
    assert "Mira el tamaño" in prompt
    assert "2 REBANADAS" in prompt
    assert "Mira el acompañamiento" in prompt
    assert "Do not invent extra numbers" in prompt


def test_converted_graphic_intent_still_uses_graphic_image_prompt():
    from video_agent.assets.service import build_scene_image_prompt

    prompt = build_scene_image_prompt(
        {
            "id": "s05",
            "layout": "short_tip",
            "visual_type": "graphic",
            "generated_image_source_layout": "graphic_comparison",
            "asset_strategy": "graphic_fallback",
            "on_screen_text": "SEGÚN EL PLATO",
            "visual_prompt": "Comparison graphic over a real kitchen table.",
            "layout_payload": {
                "title": "SEGÚN EL PLATO",
                "left": {"heading": "DESAYUNO", "text": "1–2 rebanadas"},
                "right": {"heading": "COMIDA", "text": "Trozo pequeño"},
            },
        },
        "bread plate comparison",
    )

    assert "Scene layout: graphic_comparison" in prompt
    assert "LEFT PANEL" in prompt
    assert "DESAYUNO" in prompt
    assert "RIGHT PANEL" in prompt
    assert "COMIDA" in prompt
    assert "No readable signage" not in prompt


def test_lifestyle_image_prompt_includes_required_visual_evidence_without_text_overlay():
    from video_agent.assets.service import build_scene_image_prompt
    from video_agent.browser_worker.drivers.chatgpt_image import build_image_gen_prompt

    prompt = build_scene_image_prompt(
        {
            "id": "s02",
            "layout": "short_tip",
            "on_screen_text": "MIRA TU CONTEXTO",
            "visual_prompt": "Adult prepares a breakfast plate with bread beside protein and fruit.",
            "narration": "Mira tu contexto antes de repetir pan.",
            "asset_strategy": "ai_image_preferred",
            "required_visual_evidence": {
                "required_actions": ["Adult checks bread portion before eating."],
                "required_objects": ["Clearly visible bread", "Complete breakfast plate"],
                "visibility": ["Bread count is unmistakable in the first frame."],
                "forbidden_context": ["No calorie numbers", "No weighing scale"],
            },
        },
        "breakfast bread context",
    )

    full_prompt = build_image_gen_prompt(prompt, aspect_ratio="9:16")

    assert "photorealistic lifestyle image" in prompt
    assert "Required visual evidence" in prompt
    assert "Adult checks bread portion before eating." in prompt
    assert "Clearly visible bread" in prompt
    assert "No calorie numbers" in prompt
    assert "No readable signage, captions, UI, numbers" in prompt
    assert "MIRA TU CONTEXTO" not in prompt
    assert "no text overlays" in full_prompt
    assert "no watermark" in full_prompt


def test_graphic_layout_has_no_graphic_fallback_when_chatgpt_is_unavailable():
    scene = {
        **_scene(),
        "layout": "graphic_checklist",
        "layout_payload": {"title": "REVISA ESTO", "items": ["Uno", "Dos"]},
    }
    svc = _service({"pexels_video": [], "pexels": []}, image_gen_fn=None)

    assert svc.get_scene_asset(scene, channel_id="ch", job_id="job") is None


def test_placeholder_image_meets_aesthetic_targets(tmp_path):
    import numpy as np
    from PIL import Image

    from video_agent.stages.assets import _write_placeholder_image

    tmp_img_path = tmp_path / "test_placeholder_aesthetic.jpg"
    palette = {
        "background": "#F6F1E8",
        "primary": "#2F6B57",
        "secondary": "#D98C5F",
        "accent": "#F5C24B",
        "text": "#26332F"
    }
    scene = {"layout": "short_hook", "on_screen_text": "GIRA EL PAQUETE"}

    _write_placeholder_image(tmp_img_path, scene, index=0, palette=palette, is_portrait=True)

    img = Image.open(tmp_img_path)
    assert img.size == (1080, 1920), f"Image size {img.size} is not 1080x1920"

    gray = img.convert("L")
    pixels = np.array(gray)

    mean_lum = pixels.mean()
    black_ratio = (pixels < 25).sum() / pixels.size
    near_black_ratio = (pixels < 35).sum() / pixels.size

    assert mean_lum >= 60, f"Mean luminance {mean_lum} is too dark (target >= 60)"
    assert black_ratio < 0.05, f"Black pixel ratio {black_ratio} is too high (target < 5%)"
    assert near_black_ratio < 0.10, f"Near-black pixel ratio {near_black_ratio} is too high (target < 10%)"


def test_placeholder_video_frame_meets_aesthetic_targets(tmp_path):
    import subprocess

    import numpy as np
    from PIL import Image

    from video_agent.stages.assets import _write_placeholder_video

    tmp_vid_path = tmp_path / "test_placeholder_aesthetic.mp4"
    tmp_frame_path = tmp_path / "test_placeholder_aesthetic_frame.jpg"
    palette = {
        "background": "#F6F1E8",
        "primary": "#2F6B57",
        "secondary": "#D98C5F",
        "accent": "#F5C24B",
        "text": "#26332F"
    }
    scene = {"layout": "short_hook", "on_screen_text": "GIRA EL PAQUETE"}

    _write_placeholder_video(tmp_vid_path, scene, index=0, palette=palette, duration_sec=1.0, is_portrait=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(tmp_vid_path),
        "-vframes", "1", "-f", "image2", str(tmp_frame_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    img = Image.open(tmp_frame_path)
    assert img.size == (1080, 1920), f"Video frame size {img.size} is not 1080x1920"

    gray = img.convert("L")
    pixels = np.array(gray)

    mean_lum = pixels.mean()
    black_ratio = (pixels < 25).sum() / pixels.size
    near_black_ratio = (pixels < 35).sum() / pixels.size

    assert mean_lum >= 60, f"Mean luminance {mean_lum} is too dark (target >= 60)"
    assert black_ratio < 0.05, f"Black pixel ratio {black_ratio} is too high (target < 5%)"
    assert near_black_ratio < 0.10, f"Near-black pixel ratio {near_black_ratio} is too high (target < 10%)"


def test_missing_orientation_metadata_defaults_to_portrait_for_shorts(tmp_path):
    from video_agent.stages.assets import prepare_assets

    scene_doc = {
        "total_duration_sec": 3,
        "scenes": [
            {
                "id": "s01",
                "layout": "short_hook",
                "on_screen_text": "GIRA EL PAQUETE",
                "visual_prompt": "some prompt",
                "asset_refs": {}
            }
        ]
    }

    palette = {
        "palette": {
            "background": "#F6F1E8",
            "primary": "#2F6B57",
            "secondary": "#D98C5F",
            "accent": "#F5C24B",
            "text": "#26332F"
        }
    }

    class GraphicFallbackStockClient:
        def search(self, provider, query, filters):
            return {}
        def normalize(self, provider, response):
            return []

    job_dir = tmp_path / "shorts" / "short-05"

    manifest = prepare_assets(
        job_dir=job_dir,
        style_dna=palette,
        scene_doc=scene_doc,
        visual_config={
            "strategy": "stock_photo_api",
            "providers": ["pexels"],
            "query_cache_path": str(tmp_path / "caches" / "query_cache.db"),
            "asset_library_path": str(tmp_path / "asset_library"),
        },
        stock_client=GraphicFallbackStockClient(),
        image_gen_fn=lambda p, o: None
    )

    bg_path = Path(manifest["scenes"][0]["background"])
    assert bg_path.exists()

    tmp_frame_path = tmp_path / "shorts" / "temp_frame.jpg"
    tmp_frame_path.parent.mkdir(parents=True, exist_ok=True)
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-i", str(bg_path),
        "-vframes", "1", "-f", "image2", str(tmp_frame_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    from PIL import Image
    img = Image.open(tmp_frame_path)
    assert img.size == (1080, 1920), f"Short fallback size {img.size} is not 1080x1920"



def test_graphic_scene_uses_chatgpt_image_by_default(tmp_path):
    """graphic_* scenes become generated-image scenes so Shorts do not render
    rigid paper graphic cards."""
    from PIL import Image

    from video_agent.stages.assets import prepare_assets

    gen_calls = []

    def _img_gen(prompt, out_path):
        gen_calls.append(prompt)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1920), (20, 30, 40)).save(out_path)

    scene_doc = {
        "total_duration_sec": 3,
        "scenes": [{
            "id": "s01", "layout": "graphic_routine_split",
            "on_screen_text": "MICRO PAUSAS", "visual_prompt": "p",
            "visual_importance": "critical", "asset_refs": {},
        }],
    }
    palette = {"palette": {"background": "#F6F1E8", "primary": "#2F6B57", "secondary": "#D98C5F", "accent": "#F5C24B", "text": "#26332F"}}

    class _EmptyStock:
        def search(self, provider, query, filters): return {}
        def normalize(self, provider, response): return []

    manifest = prepare_assets(
        job_dir=tmp_path / "jobs" / "job-graph-skip",
        style_dna=palette, scene_doc=scene_doc,
        visual_config={"strategy": "stock_photo_api", "providers": ["pexels"],
                       "query_cache_path": str(tmp_path / "c.db"),
                       "asset_library_path": str(tmp_path / "lib")},
        stock_client=_EmptyStock(), image_gen_fn=_img_gen,
    )

    assert len(gen_calls) == 1
    assert "MICRO PAUSAS" in gen_calls[0]
    assert scene_doc["scenes"][0]["asset_refs"]["background"] != ""
    assert scene_doc["scenes"][0]["layout"] == "short_tip"
    assert manifest["scenes"][0]["background_source"] == "ChatGPT infographic"


def test_chatgpt_lifestyle_image_label_for_non_graphic_ai():
    from video_agent.stages.assets import _background_source_label

    assert _background_source_label({
        "provider": "ai_generated",
        "asset_tier": "ai_image",
    }) == "ChatGPT lifestyle image"


def test_graphic_scene_with_video_blur_still_acquires(tmp_path):
    from video_agent.stages.assets import prepare_assets

    gen_calls = []

    def _img_gen(prompt, out_path):
        gen_calls.append(out_path)
        from PIL import Image
        Image.new("RGB", (1080, 1920), (20, 30, 40)).save(out_path)

    scene_doc = {
        "total_duration_sec": 3,
        "scenes": [{
            "id": "s01", "layout": "graphic_routine_split",
            "on_screen_text": "MICRO PAUSAS", "visual_prompt": "p",
            "visual_importance": "critical", "asset_refs": {},
            "layout_payload": {"background_mode": "video_blur"},
        }],
    }
    palette = {"palette": {"background": "#F6F1E8", "primary": "#2F6B57", "secondary": "#D98C5F", "accent": "#F5C24B", "text": "#26332F"}}

    class _EmptyStock:
        def search(self, provider, query, filters): return {}
        def normalize(self, provider, response): return []

    prepare_assets(
        job_dir=tmp_path / "jobs" / "job-graph-blur",
        style_dna=palette, scene_doc=scene_doc,
        visual_config={"strategy": "stock_photo_api", "providers": ["pexels"],
                       "query_cache_path": str(tmp_path / "c.db"),
                       "asset_library_path": str(tmp_path / "lib")},
        stock_client=_EmptyStock(), image_gen_fn=_img_gen,
    )
    # video_blur explicitly wants media -> AI fallback runs (critical scene).
    assert len(gen_calls) >= 1
