"""Single policy owner for Shorts background-music selection."""
from __future__ import annotations

import unicodedata

# Pillar / topic → music-library track key. See spec §15.1.
_PILLAR_TO_TRACK = {
    "movement": "shorts_movement",
    "exercise": "shorts_movement",
    "routine": "shorts_movement",
    "food": "shorts_daily_habit",
    "daily_habits": "shorts_daily_habit",
    "movement_light": "shorts_daily_habit",
    "sleep": "shorts_sleep_stress",
    "stress": "shorts_sleep_stress",
    "calm": "shorts_sleep_stress",
    "mental_load": "shorts_sleep_stress",
    "energy": "shorts_sleep_stress",
    "menopause": "shorts_sleep_stress",
    "sleep_deep": "shorts_deep_calm",
    "reflective": "shorts_deep_calm",
    "night": "shorts_deep_calm",
}

FALLBACK_TRACK = "shorts_sleep_stress"

# bug-526: Shorts ideas carry no canonical pillar, so the raw SPANISH title
# reaches the selector and the exact-match lookup always missed — every
# infographic Short played the fallback track. Derive the pillar from Spanish
# keywords (accent-insensitive substring match on normalized text) before
# falling back. Order matters: the first pillar with a keyword hit wins, and
# sleep/stress outranks food so "cena ligera para dormir mejor" reads as a
# sleep topic, not a food one.
_SPANISH_PILLAR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("menopause", ("menopausia",)),
    ("sleep", ("dormir", "sueno", "insomnio", "descans", "acostar", "siesta", "trasnochar")),
    ("stress", ("estres", "ansiedad", "calma", "carga mental", "preocup", "relaj")),
    ("exercise", (
        "ejercicio", "caminar", "caminata", "movimiento", "musculo", "fuerza",
        "estirar", "estiramiento", "entrena", "sarcopenia", "flexibilidad",
    )),
    ("food", (
        "pan", "comida", "aliment", "comer", "desayun", "cena", "merienda",
        "receta", "nutri", "fruta", "verdura", "azucar", "proteina", "fibra",
        "aceite", "cafe", "integral", "etiqueta", "ingrediente", "plato",
        "cocina", "vitamina", "grasa", "sal ", "legumbre", "cereal",
        "tostada", "yogur", "avena", "huevo", "queso", "leche", "carne",
        "pescado", "ensalada", "sopa", "batido", "hidrata", "agua", "snack",
        "picoteo", "porcion", "saciedad", "satisfecho", "hambre", "digestion",
    )),
    ("energy", ("energia", "cansancio", "fatiga", "vitalidad")),
)


def _normalize(text: str) -> str:
    """Lowercase and strip accents so 'ALIMENTACIÓN' matches 'aliment'."""
    lowered = str(text or "").strip().lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch)
    )


def derive_pillar_from_text(text: str) -> str:
    """Canonical pillar for free Spanish topic text; '' when nothing matches.

    Substring match on accent-normalized text; word boundaries are deliberately
    loose ('aliment' catches alimento/alimentación/alimentar) — false positives
    only ever change the background track, never the content."""
    normalized = f" {_normalize(text)} "
    for pillar, keywords in _SPANISH_PILLAR_KEYWORDS:
        if any(kw in normalized for kw in keywords):
            return pillar
    return ""


def select_music_track(pillar_or_topic: str, channel_config: dict) -> str:
    """Return a music-library track key for the detected pillar/topic.

    Accepts either a canonical pillar key ('food') or free Spanish topic text
    ('5 tipos de pan...'); free text is mapped to a pillar via
    ``derive_pillar_from_text`` before the fallback applies (bug-526)."""
    key = str(pillar_or_topic or "").strip().lower()
    if key not in _PILLAR_TO_TRACK:
        derived = derive_pillar_from_text(key)
        if derived:
            key = derived
    track = _PILLAR_TO_TRACK.get(key, FALLBACK_TRACK)
    # Honour a track only if it exists in the library; else fall back.
    tracks = ((channel_config.get("music_library") or {}).get("tracks")) or {}
    if tracks and track not in tracks and FALLBACK_TRACK in tracks:
        return FALLBACK_TRACK
    return track
