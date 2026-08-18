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
    assert scenes["scenes"][0]["on_screen_text"] == "Calma"


def test_mock_scene_visual_prompts_include_topic_and_key_points():
    provider = MockProvider()
    script = provider.generate_script(CHANNEL, IDEA, "job-1")
    scenes = provider.generate_scenes(CHANNEL, IDEA, script, "job-1")

    first_prompt = scenes["scenes"][0]["visual_prompt"]
    assert IDEA["topic"] in first_prompt
    assert IDEA["key_points"][0] in first_prompt


def test_mock_seo_passes_thumbnail_title_qa():
    provider = MockProvider()
    seo = provider.generate_seo(CHANNEL, IDEA, "jobs/demo/thumbnail.jpg")
    qa = check_thumbnail_title(seo, CHANNEL)
    assert qa["verdict"] == "PASS"
    assert seo["ai_disclosure"] is True


def test_thumbnail_qa_measures_thumbnail_text_not_title():
    seo = {
        "title": "Título breve",
        "thumbnail_text": "UNO DOS TRES CUATRO CINCO SEIS SIETE",
        "ai_disclosure": True,
    }

    qa = check_thumbnail_title(seo, CHANNEL)

    assert qa["verdict"] == "REVISE"
    assert qa["issues"][0]["type"] == "THUMBNAIL_TEXT_DENSE"


def test_thumbnail_qa_checks_every_generated_variant():
    seo = {
        "title": "Título breve",
        "thumbnail_text": "DUERME MEJOR HOY",
        "title_variants": [
            {"title": "Variante uno", "thumbnail_text": "DUERME MEJOR HOY"},
            {
                "title": "Variante dos",
                "thumbnail_text": "UNO DOS TRES CUATRO CINCO SEIS SIETE",
            },
        ],
        "ai_disclosure": True,
    }

    qa = check_thumbnail_title(seo, CHANNEL)

    assert qa["verdict"] == "REVISE"
    assert qa["issues"][0]["type"] == "THUMBNAIL_TEXT_DENSE"
    assert "variant 2" in qa["issues"][0]["message"].lower()


def test_thumbnail_qa_accepts_six_word_copy_for_every_variant():
    seo = {
        "title": "Título claro y breve",
        "thumbnail_text": "PAN O PATATA QUÉ ELEGIR HOY",
        "title_variants": [
            {
                "title": "Variante uno",
                "thumbnail_text": "PAN O PATATA QUÉ ELEGIR HOY",
            },
            {
                "title": "Variante dos",
                "thumbnail_text": "EVITA PESADEZ AL ELEGIR TUS CARBOHIDRATOS",
            },
        ],
        "ai_disclosure": True,
    }

    qa = check_thumbnail_title(seo, CHANNEL)

    assert qa["verdict"] == "PASS"


def test_thumbnail_qa_rejects_selected_copy_shorter_than_three_words():
    seo = {
        "title": "Título claro y breve",
        "thumbnail_text": "POCAS PALABRAS",
        "ai_disclosure": True,
    }

    qa = check_thumbnail_title(seo, CHANNEL)

    assert qa["verdict"] == "REVISE"
    assert qa["issues"][0]["type"] == "THUMBNAIL_TEXT_SPARSE"
    assert "selected thumbnail_text" in qa["issues"][0]["message"]


def test_thumbnail_qa_rejects_sparse_copy_in_every_variant():
    seo = {
        "title": "Título claro y breve",
        "thumbnail_text": "DUERME MEJOR HOY",
        "title_variants": [
            {"title": "Variante uno", "thumbnail_text": "DUERME MEJOR HOY"},
            {"title": "Variante dos", "thumbnail_text": "PAN"},
        ],
        "ai_disclosure": True,
    }

    qa = check_thumbnail_title(seo, CHANNEL)

    assert qa["verdict"] == "REVISE"
    assert qa["issues"][0]["type"] == "THUMBNAIL_TEXT_SPARSE"
    assert "variant 2" in qa["issues"][0]["message"]
