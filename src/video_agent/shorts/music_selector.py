"""Single policy owner for Shorts background-music selection."""
from __future__ import annotations

import re
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

# bug-526: Shorts ideas carry no canonical pillar, so raw SPANISH titles reach
# the selector and the exact-match lookup always missed — every infographic
# Short played the fallback track. Derive the pillar from Spanish vocabulary,
# matching WHOLE WORDS (or controlled word STEMS via prefix on whole tokens),
# never free substrings — "pan" must match the word "pan" but not "pantalla".
# Order matters: the first pillar with a hit wins, and sleep/stress outranks
# food so "cena ligera para dormir mejor" reads as a sleep topic.
_SPANISH_PILLAR_VOCABULARY: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    ("menopause", frozenset({"menopausia"}), ()),
    (
        "sleep",
        frozenset({"dormir", "sueno", "insomnio", "siesta", "madrugada"}),
        ("descans", "acost", "trasnoch", "duerm"),
    ),
    (
        "stress",
        frozenset({"estres", "ansiedad", "calma", "nervios"}),
        ("preocup", "relaj", "tension"),
    ),
    (
        "exercise",
        frozenset({"ejercicio", "ejercicios", "movimiento", "fuerza", "sarcopenia"}),
        ("muscul", "estir", "entren", "flexib", "camin"),
    ),
    (
        "food",
        frozenset({
            "pan", "panes", "comida", "comidas", "comer", "cena", "cenas", "receta",
            "recetas", "fruta", "frutas", "verdura", "verduras", "fibra", "aceite",
            "cafe", "integral", "etiqueta", "etiquetas", "plato", "platos", "sal",
            "agua", "leche", "carne", "pescado", "ensalada", "sopa", "batido",
            "tostada", "tostadas", "yogur", "avena", "huevo", "huevos", "queso",
            "snack", "hambre", "satisfecho", "satisfecha", "envase", "supermercado",
        }),
        (
            "aliment", "desayun", "meriend", "nutri", "azucar", "protein",
            "ingredient", "cocin", "vitamin", "gras", "legumbr", "cereal",
            "digest", "porcion", "saci", "mastic",
        ),
    ),
    ("energy", frozenset({"energia", "cansancio", "fatiga", "vitalidad"}), ()),
)

_TOKEN_RE = re.compile(r"[a-z]+")


def _normalize(text: str) -> str:
    """Lowercase and strip accents so 'ALIMENTACIÓN' matches the 'aliment' stem."""
    lowered = str(text or "").strip().lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch)
    )


def derive_pillar_from_text(text: str) -> str:
    """Canonical pillar for free Spanish topic text; '' when nothing matches.

    Tokenizes the accent-normalized text and matches whole words or controlled
    stems against whole tokens only — no free substring matching, so 'pan'
    never fires on 'pantalla' and 'sal' never fires on 'salir' (stems apply
    prefix-wise to tokens, exact words must equal the token)."""
    tokens = _TOKEN_RE.findall(_normalize(text))
    if not tokens:
        return ""
    token_set = set(tokens)
    for pillar, exact_words, stems in _SPANISH_PILLAR_VOCABULARY:
        if token_set & exact_words:
            return pillar
        if stems and any(tok.startswith(stem) for tok in tokens for stem in stems):
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
