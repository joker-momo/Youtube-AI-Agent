from __future__ import annotations

from typing import Any

from video_agent.qa.common import average_sentence_words, pass_result, revise_result

BLOCKED_TERMS = ("cura milagrosa", "diagnosticar", "dosis exacta")


def check_script(script: dict[str, Any], channel_config: dict[str, Any]) -> dict[str, Any]:
    hook_words = len(script.get("hook", "").split())
    if hook_words > 28:
        return revise_result("HOOK_TOO_LONG", "Hook must stay under 28 words.", "rewrite_hook_only")
    narration = script.get("narration", "").lower()
    for term in BLOCKED_TERMS:
        if term in narration:
            return revise_result("MEDICAL_SAFETY", f"Blocked medical phrase found: {term}", "rewrite_unsafe_sentence")
    max_average = channel_config.get("qa_rules", {}).get("thresholds", {}).get("max_average_sentence_words", 15)
    avg = average_sentence_words(script.get("narration", ""))
    if avg > max_average:
        return revise_result("SENTENCES_TOO_LONG", f"Average sentence length is {avg:.1f} words.", "shorten_sentences")
    if "profesional de salud" not in narration:
        return revise_result("MISSING_DISCLAIMER", "Health content needs an educational disclaimer.", "add_disclaimer")
    return pass_result({"hook_words": hook_words, "average_sentence_words": round(avg)})
