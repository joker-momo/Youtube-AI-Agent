from video_agent.providers.mock import MockProvider
from video_agent.qa.scene_qa import check_scenes
from video_agent.qa.script_qa import check_script
from video_agent.qa.thumbnail_title_qa import check_thumbnail_title


CHANNEL = {
    "channel": {"id": "vida-plena-45", "name": "Vida Plena 45+"},
    "audience": {"language": "es-LA"},
    "upload": {"ai_disclosure": True},
    "qa_rules": {"thresholds": {"max_average_sentence_words": 15, "max_thumbnail_words": 6}},
}

IDEA = {
    "topic": "Habitos nocturnos para dormir mejor despues de los 45",
    "angle": "Rutina simple y segura",
    "target_duration_sec": 54,
    "key_points": ["calma", "pantallas", "respiracion", "horarios", "consulta profesional"],
    "title_seed": "5 habitos nocturnos para dormir mejor despues de los 45",
}


def test_mock_script_passes_script_qa():
    provider = MockProvider()
    script = provider.generate_script(CHANNEL, IDEA, "job-1")
    qa = check_script(script, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert script["channel_id"] == "vida-plena-45"
    assert "profesional de salud" in script["narration"]


def test_mock_scenes_pass_scene_qa():
    provider = MockProvider()
    script = provider.generate_script(CHANNEL, IDEA, "job-1")
    scenes = provider.generate_scenes(CHANNEL, IDEA, script, "job-1")
    qa = check_scenes(scenes, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert 45 <= scenes["total_duration_sec"] <= 60
    assert len(scenes["scenes"]) == 5


def test_mock_seo_passes_thumbnail_title_qa():
    provider = MockProvider()
    seo = provider.generate_seo(CHANNEL, IDEA, "jobs/demo/thumbnail.jpg")
    qa = check_thumbnail_title(seo, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert seo["ai_disclosure"] is True
