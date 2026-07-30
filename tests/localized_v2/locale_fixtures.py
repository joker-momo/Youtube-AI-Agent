from __future__ import annotations

from copy import deepcopy
from typing import Any

LOCALE_DATA = {
    "en-US": {
        "language": "English",
        "market": "United States",
        "address": "you",
        "soft": "research suggests",
        "prohibited": "cures disease",
        "prefer": "healthy aging",
        "voice": ("kokoro", "a", "af_heart"),
        "font": "Inter",
        "metric": (5.0, 1.0),
    },
    "fr-FR": {
        "language": "français",
        "market": "France",
        "address": "vous",
        "soft": "les recherches suggèrent",
        "prohibited": "guérit la maladie",
        "prefer": "bien vieillir",
        "voice": ("melo", "FR", "FR_0"),
        "font": "Noto Sans",
        "metric": (5.2, 1.15),
    },
    "pt-BR": {
        "language": "português brasileiro",
        "market": "Brasil",
        "address": "você",
        "soft": "estudos sugerem",
        "prohibited": "cura a doença",
        "prefer": "envelhecimento saudável",
        "voice": ("kokoro", "p", "pf_dora"),
        "font": "Noto Sans",
        "metric": (5.1, 1.08),
    },
    "ko-KR": {
        "language": "한국어",
        "market": "대한민국",
        "address": "시청자 여러분",
        "soft": "연구에 따르면",
        "prohibited": "질병을 치료합니다",
        "prefer": "건강한 노화",
        "voice": ("melo", "KR", "KR"),
        "font": "Noto Sans KR",
        "metric": (2.4, 1.0),
    },
    "ja-JP": {
        "language": "日本語",
        "market": "日本",
        "address": "皆さん",
        "soft": "研究では",
        "prohibited": "病気を治します",
        "prefer": "健やかな年齢の重ね方",
        "voice": ("melo", "JP", "JP"),
        "font": "Noto Sans JP",
        "metric": (2.2, 1.0),
    },
}


def locale_pack(locale: str) -> dict[str, Any]:
    data = LOCALE_DATA[locale]
    return {
        "schemaVersion": "localized-locale-v2/v1",
        "locale": locale,
        "language": data["language"],
        "market": data["market"],
        "audienceAddress": {"formal": locale != "en-US", "preferred": data["address"]},
        "lexicalPreferences": {
            "prefer": [data["prefer"]],
            "avoid": [data["prohibited"]],
        },
        "measurement": {
            "system": "us" if locale == "en-US" else "metric",
            "temperature": "fahrenheit" if locale == "en-US" else "celsius",
        },
        "dates": {
            "order": "MDY" if locale == "en-US" else ("YMD" if locale in {"ko-KR", "ja-JP"} else "DMY")
        },
        "numbers": {"decimalSeparator": "." if locale in {"en-US", "ko-KR", "ja-JP"} else ","},
        "medicalSafety": {
            "softClaims": [data["soft"]],
            "prohibitedClaims": [data["prohibited"], "guaranteed result"],
            "disclaimer": f"{data['language']} educational information only.",
        },
        "seo": {
            "titleMaxChars": 70,
            "keywordStyle": "natural and locally specific",
            "keywordCues": [data["prefer"]],
            "thumbnailMaxChars": 30,
            "pinnedCommentStyle": "warm and concise",
        },
        "narration": {"wordsPerMinute": 125, "sentenceMaxWords": 22},
        "fonts": {
            "families": [data["font"]],
            "requiredCodepoints": ["0041"],
        },
        "visuals": {
            "peopleContext": f"ordinary adults in {data['market']} when locally relevant",
            "avoid": ["stereotypes", "irrelevant ceremonial clothing"],
        },
        "textMetrics": {
            "charsPerWord": data["metric"][0],
            "expansionRatio": data["metric"][1],
        },
    }


def channel(locale: str) -> dict[str, Any]:
    provider, language, voice_id = LOCALE_DATA[locale]["voice"]
    slug = locale.lower().replace("-", "-")
    rollout_order = tuple(LOCALE_DATA).index(locale) + 1
    return {
        "schemaVersion": "localized-channel-v2/v1",
        "enabled": True,
        "rolloutOrder": rollout_order,
        "channelId": f"healthy-life-{slug}",
        "locale": locale,
        "brand": {
            "name": f"Healthy Life {locale}",
            "introClip": f"brand/{locale}/intro.mp4",
            "disclaimerClip": f"brand/{locale}/disclaimer.mp4",
            "outroClip": f"brand/{locale}/outro.mp4",
        },
        "voice": {
            "provider": provider,
            "language": language,
            "voiceId": voice_id,
            "speed": 1.0,
        },
        "render": {
            "composition": "LocalizedV2ChannelVideo",
            "concurrency": "auto",
            "subtitles": {"enabled": False},
        },
        "content": {"type": "long_form", "targetDurationSec": 840},
        "canary": {
            "status": "APPROVED",
            "checks": {
                "audio": "PASS",
                "font": "PASS",
                "render": "PASS",
                "humanReview": "PASS",
                "dashboardLifecycle": "PASS",
            },
            "evidence": [
                f"canary/{locale}/audio.json",
                f"canary/{locale}/font.json",
                f"canary/{locale}/render.json",
                f"canary/{locale}/human-review.json",
                f"canary/{locale}/dashboard-lifecycle.json",
            ],
        },
    }


def snapshots(locale: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return deepcopy(channel(locale)), deepcopy(locale_pack(locale))
