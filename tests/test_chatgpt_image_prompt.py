"""Image-gen prompt must enforce 1920x1080 (Full HD, 16:9 landscape)."""
from video_agent.browser_worker.drivers.chatgpt_image import (
    IMAGE_GEN_INSTRUCTION,
    build_image_gen_prompt,
)


def test_instruction_enforces_1920x1080_landscape():
    text = IMAGE_GEN_INSTRUCTION.lower()
    assert "1920" in text and "1080" in text
    assert "16:9" in text
    assert "landscape" in text


def test_build_image_gen_prompt_prepends_instruction_and_keeps_user_prompt():
    user = "a calm bedroom at dawn"
    full = build_image_gen_prompt(user)
    assert full.startswith(IMAGE_GEN_INSTRUCTION)
    assert user in full
    # size must appear before the user content
    assert full.index("1920") < full.index(user)


def test_build_image_gen_prompt_strips_user_prompt():
    assert build_image_gen_prompt("  hi  ").endswith("hi")
