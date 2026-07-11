"""bug-526: ideas carry no pillar, so the raw SPANISH topic/title reaches
music_selector and the exact-match lookup always missed -> every infographic
Short got the fallback track. The selector must derive a canonical pillar from
Spanish topic text before falling back."""
from __future__ import annotations

from video_agent.shorts import music_selector

CFG = {
    "music_library": {
        "tracks": {
            "shorts_movement": {"file": "a.mp3", "title": "Find Your Way"},
            "shorts_daily_habit": {"file": "b.mp3", "title": "Fresh Fallen Snow"},
            "shorts_sleep_stress": {"file": "c.mp3", "title": "Floating Home"},
            "shorts_deep_calm": {"file": "d.mp3", "title": "Ether"},
        }
    }
}


def test_exact_canonical_pillar_still_wins():
    assert music_selector.select_music_track("food", CFG) == "shorts_daily_habit"
    assert music_selector.select_music_track("sleep", CFG) == "shorts_sleep_stress"


def test_spanish_food_topic_maps_to_daily_habit_track():
    key = music_selector.select_music_track(
        "5 tipos de pan y el detalle que debes comprobar", CFG
    )
    assert key == "shorts_daily_habit"


def test_spanish_sleep_stress_topic_maps_to_sleep_track():
    key = music_selector.select_music_track(
        "Rutina sencilla para dormir mal por estrés después de los 45", CFG
    )
    assert key == "shorts_sleep_stress"


def test_spanish_exercise_topic_maps_to_movement_track():
    key = music_selector.select_music_track(
        "Ejercicio después de los 45: cómo empezar suave", CFG
    )
    assert key == "shorts_movement"


def test_unknown_topic_still_falls_back():
    assert (
        music_selector.select_music_track("historia del imperio romano", CFG)
        == music_selector.FALLBACK_TRACK
    )


def test_accented_and_uppercase_spanish_text_is_normalized():
    key = music_selector.select_music_track("ALIMENTACIÓN saludable con café", CFG)
    assert key == "shorts_daily_habit"


def test_real_idea_titles_from_the_pan_job_all_map_to_food():
    """bug-526 round 2: idea-08's real title had no keyword hit ('tostada',
    'satisfecho' missing) and silently fell back. Every food-adjacent idea title
    from the live pan job must resolve to the food track."""
    titles = [
        "5 pasos para montar una tostada que te deje satisfecho",
        "6 combinaciones sencillas para que el pan forme una comida",
        "5 tipos de pan y el detalle que debes comprobar",
        "Lo que promete el envase frente a lo que revela la etiqueta",
        "5 mitos sobre el pan que complican tus decisiones",
    ]
    for title in titles:
        assert (
            music_selector.select_music_track(title, CFG) == "shorts_daily_habit"
        ), title
