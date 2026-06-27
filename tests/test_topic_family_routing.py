import pytest
from video_agent.contracts import TopicFamily, resolve_topic_family

def test_resolve_topic_family_explicit():
    script_dict = {"topic_family": "movement"}
    assert resolve_topic_family(script_dict) == TopicFamily.MOVEMENT
    
    script_dict = {"topic": " SLEEP "}
    assert resolve_topic_family(script_dict) == TopicFamily.SLEEP

def test_resolve_topic_family_mapped_pillar():
    # Should ignore explicit if invalid and fall back to mapped pillar (actually explicit will just try and pass if invalid)
    # Wait, in the implementation:
    # try: return TopicFamily(explicit) except ValueError: pass
    
    script_dict = {"topic_family": "INVALID", "pillar": "pan"}
    assert resolve_topic_family(script_dict) == TopicFamily.NUTRITION
    
    script_dict = {"pillar": "ejercicio"}
    assert resolve_topic_family(script_dict) == TopicFamily.MOVEMENT
    
    script_dict = {"pillar": "stress"}
    assert resolve_topic_family(script_dict) == TopicFamily.MENTAL_LOAD

def test_resolve_topic_family_classifier():
    script_dict = {
        "hook": "Cómo hacer sentadilla perfecta",
        "narration": "El movimiento es clave",
        "title": "Sentadilla"
    }
    assert resolve_topic_family(script_dict) == TopicFamily.MOVEMENT
    
    script_dict = {
        "hook": "Tienes problemas de insomnio?",
        "narration": "Dormir bien es importante",
        "title": "Descanso nocturno"
    }
    assert resolve_topic_family(script_dict) == TopicFamily.SLEEP


def test_resolve_topic_family_food_context_beats_incidental_sleep_word():
    script_dict = {
        "hook": "TU MANO AYUDA",
        "narration": (
            "TU MANO AYUDA. Uno: depende de apetito, actividad, sueño, objetivos y resto "
            "del plato. Dos: para desayunar, una o dos rebanadas según tamaño."
        ),
        "title": "",
    }

    assert resolve_topic_family(script_dict) == TopicFamily.NUTRITION

def test_resolve_topic_family_general_fallback(caplog):
    script_dict = {
        "hook": "Algo totalmente distinto",
        "narration": "No hay keywords",
        "title": "Un video"
    }
    assert resolve_topic_family(script_dict) == TopicFamily.GENERAL
    assert "Could not determine TopicFamily; defaulting to GENERAL." in caplog.text
