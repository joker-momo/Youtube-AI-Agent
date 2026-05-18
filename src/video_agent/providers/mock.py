from __future__ import annotations

from typing import Any


class MockProvider:
    def generate_script(self, channel_config: dict[str, Any], idea: dict[str, Any], job_id: str) -> dict[str, Any]:
        channel_id = channel_config["channel"]["id"]
        key_points = idea["key_points"]
        hook = "Te cuesta dormir bien despues de los 45? Empieza con una noche mas tranquila."
        sections = [
            {
                "title": "Prepara el descanso",
                "text": f"Una hora antes de dormir, baja el ritmo. {key_points[0].capitalize()} ayuda a tu cuerpo a reconocer que el dia termino.",
            },
            {
                "title": "Cuida los estimulos",
                "text": f"Evita pantallas brillantes y cenas muy pesadas. {key_points[1].capitalize()} permite que el sueno llegue con menos resistencia.",
            },
            {
                "title": "Respira con suavidad",
                "text": f"Prueba una respiracion lenta por dos minutos. {key_points[2].capitalize()} invita al cuerpo a calmarse.",
            },
            {
                "title": "Manten constancia",
                "text": f"Intenta acostarte a una hora parecida cada noche. {key_points[3].capitalize()} crea una senal simple y repetible.",
            },
            {
                "title": "Busca apoyo si persiste",
                "text": f"Si el insomnio continua, habla con un profesional. {key_points[4].capitalize()} es parte de cuidarte con responsabilidad.",
            },
        ]
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
        durations = [10, 11, 11, 11, max(9, target - 43)]
        scene_texts = [
            ("Una noche mas tranquila", script["hook"], "slow_push"),
            ("Baja el ritmo", script["sections"][0]["text"], "pan_left"),
            ("Menos pantallas", script["sections"][1]["text"], "pan_right"),
            ("Respira suave", script["sections"][2]["text"], "slow_zoom"),
            ("Constancia y apoyo", script["sections"][4]["text"], "fade_up"),
        ]
        scenes = []
        for index, (text, narration, motion) in enumerate(scene_texts, start=1):
            scenes.append(
                {
                    "id": f"scene-{index:02d}",
                    "duration_sec": durations[index - 1],
                    "narration": narration,
                    "visual_type": "generated_placeholder",
                    "visual_prompt": f"Warm editorial wellness scene for adults 45+, {text.lower()}, calm home environment",
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

    def generate_seo(self, channel_config: dict[str, Any], idea: dict[str, Any], thumbnail_path: str) -> dict[str, Any]:
        title = idea.get("title_seed") or "5 habitos nocturnos para dormir mejor"
        return {
            "title": title,
            "description": (
                "Una rutina educativa y simple para preparar mejor la noche despues de los 45. "
                "Este contenido no reemplaza el consejo de un profesional de salud."
            ),
            "tags": ["sueno", "bienestar", "vida plena 45", "habitos saludables"],
            "language": channel_config["audience"]["language"],
            "ai_disclosure": bool(channel_config["upload"]["ai_disclosure"]),
            "thumbnail_path": thumbnail_path,
        }
