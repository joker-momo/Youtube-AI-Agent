from __future__ import annotations

from pathlib import Path
from typing import Any


def _short_label(text: str, max_words: int = 4) -> str:
    words = text.replace(",", "").split()
    label = " ".join(words[:max_words])
    return label.capitalize()


class MockProvider:
    def generate_script(self, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str) -> dict[str, Any]:
        channel_id = channel_config["channel"]["id"]
        key_points = idea["key_points"]
        hook = f"Hoy vemos {_short_label(idea['topic'], 5).lower()} con calma."
        sections = []
        for index, point in enumerate(key_points, start=1):
            if index == len(key_points):
                text = f"{point.capitalize()}. Busca apoyo profesional si hace falta."
            else:
                text = f"{point.capitalize()}. Hazlo simple y con calma."
            sections.append({"title": _short_label(point), "text": text})
        narration_parts = [hook] + [section["text"] for section in sections]
        narration_parts.append("Este contenido es educativo y no reemplaza el consejo de un profesional de salud.")
        narration = " ".join(narration_parts)
        return {
            "channel_id": channel_id,
            "job_id": job_id,
            "hook": hook,
            "sections": sections,
            "narration": narration,
            "cta": "Guarda esta rutina y compartela con alguien que quiera descansar mejor.",
            "qa": {"verdict": "PENDING", "iterations": []},
        }

    def generate_scenes(
        self,
        channel_config: dict[str, Any],
        idea: dict[str, Any],
        script: dict[str, Any],
        job_id: str,
    ) -> dict[str, Any]:
        target = int(idea["target_duration_sec"])
        topic = idea["topic"]
        key_points = idea["key_points"]
        durations = [10, 11, 11, 11, max(9, target - 43)]
        scene_texts = [
            (script["sections"][0]["title"], f"{script['hook']} {script['sections'][0]['text']}", "slow_push"),
            (script["sections"][1]["title"], script["sections"][1]["text"], "pan_left"),
            (script["sections"][2]["title"], script["sections"][2]["text"], "pan_right"),
            (script["sections"][3]["title"], script["sections"][3]["text"], "slow_zoom"),
            (script["sections"][4]["title"], script["sections"][4]["text"], "fade_up"),
        ]
        scenes = []
        for index, (text, narration, motion) in enumerate(scene_texts, start=1):
            scenes.append(
                {
                    "id": f"scene-{index:02d}",
                    "duration_sec": durations[index - 1],
                    "narration": narration,
                    "visual_type": "generated_placeholder",
                    "visual_prompt": (
                        f"Warm realistic wellness photo for adults 45+, {topic}, "
                        f"{key_points[index - 1]}, {text.lower()}, calm lifestyle environment"
                    ),
                    "on_screen_text": text,
                    "caption": narration[:130],
                    "motion": motion,
                    "asset_refs": {"background": f"assets/scene-{index:02d}.jpg"},
                }
            )
        return {
            "channel_id": channel_config["channel"]["id"],
            "job_id": job_id,
            "scenes": scenes,
            "total_duration_sec": sum(durations),
            "qa": {"verdict": "PENDING", "iterations": []},
        }

    def generate_seo(
        self,
        channel_config: dict[str, Any],
        idea: dict[str, Any],
        thumbnail_path: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        title = idea.get("title_seed") or "5 habitos nocturnos para dormir mejor"
        return {
            "job_id": job_id or Path(thumbnail_path).parent.name,
            "title": title,
            "description": (
                "Una rutina educativa y simple para preparar mejor la noche despues de los 45. "
                "Este contenido no reemplaza el consejo de un profesional de salud."
            ),
            "tags": ["sueño saludable", "bienestar 45+", "rutina nocturna", "descanso saludable", "hábitos nocturnos"],
            "language": channel_config.get("seo", {}).get("language", channel_config["audience"]["language"]),
            "ai_disclosure": bool(channel_config["upload"]["ai_disclosure"]),
            "thumbnail_path": thumbnail_path,
            "thumbnail_text": "DUERME MEJOR HOY",
        }
