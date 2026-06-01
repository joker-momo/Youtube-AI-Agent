"""Image-gen prompt must enforce 1920x1080 (Full HD, 16:9 landscape)."""
import asyncio
from pathlib import Path

import video_agent.browser_worker.drivers.chatgpt_image as chatgpt_image
from video_agent.browser_worker.drivers.chatgpt_image import (
    ChatGPTImageDriver,
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


def test_generate_image_selects_create_image_mode_before_typing(monkeypatch, tmp_path):
    events: list[str] = []
    out_path = tmp_path / "thumb.png"
    driver = ChatGPTImageDriver(page=object())
    driver._opened = True

    async def create_project(name):
        events.append(f"project:{name}")

    async def focus_composer():
        events.append("focus")

    async def select_mode():
        events.append("select-mode")

    async def fake_type(page, text):
        events.append("type")

    async def fake_pause(*args, **kwargs):
        events.append("pause")

    async def click_send():
        events.append("send")

    async def wait_for_image(timeout, exclude_urls=None):
        events.append("wait")
        return "https://example.com/image.png"

    async def download_image(src, dest):
        events.append("download")
        Path(dest).write_bytes(b"fake image")

    async def delete_project(name):
        events.append(f"delete:{name}")

    monkeypatch.setattr(driver, "_create_project", create_project)
    monkeypatch.setattr(driver, "_focus_composer", focus_composer)
    monkeypatch.setattr(driver, "_select_create_image_mode_and_aspect_ratio", select_mode, raising=False)
    monkeypatch.setattr(chatgpt_image, "human_type", fake_type)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)
    monkeypatch.setattr(driver, "_click_send", click_send)
    monkeypatch.setattr(driver, "_wait_for_image", wait_for_image)
    monkeypatch.setattr(driver, "_download_image", download_image)
    monkeypatch.setattr(driver, "delete_project", delete_project)

    asyncio.run(driver.generate_image("make a thumbnail", project_name="p", out_path=out_path))

    assert events[:4] == ["project:p", "focus", "select-mode", "type"]
    assert "send" in events


def test_generate_images_selects_create_image_mode_for_each_prompt(monkeypatch, tmp_path):
    events: list[str] = []
    driver = ChatGPTImageDriver(page=object())
    driver._opened = True

    async def create_project(name):
        events.append(f"project:{name}")

    async def focus_composer():
        events.append("focus")

    async def select_mode():
        events.append("select-mode")

    async def fake_type(page, text):
        events.append("type")

    async def fake_pause(*args, **kwargs):
        events.append("pause")

    async def click_send():
        events.append("send")

    async def wait_for_image(timeout, exclude_urls=None):
        events.append("wait")
        return f"https://example.com/image-{events.count('wait')}.png"

    async def download_image(src, dest):
        events.append("download")
        Path(dest).write_bytes(b"fake image")

    async def delete_project(name):
        events.append(f"delete:{name}")

    monkeypatch.setattr(driver, "_create_project", create_project)
    monkeypatch.setattr(driver, "_focus_composer", focus_composer)
    monkeypatch.setattr(driver, "_select_create_image_mode_and_aspect_ratio", select_mode, raising=False)
    monkeypatch.setattr(chatgpt_image, "human_type", fake_type)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)
    monkeypatch.setattr(driver, "_click_send", click_send)
    monkeypatch.setattr(driver, "_wait_for_image", wait_for_image)
    monkeypatch.setattr(driver, "_download_image", download_image)
    monkeypatch.setattr(driver, "delete_project", delete_project)
    asyncio.run(
        driver.generate_images(
            ["first", "second"],
            project_name="p",
            out_paths=[tmp_path / "1.png", tmp_path / "2.png"],
        )
    )

    assert events.count("select-mode") == 2
    assert events.index("select-mode") < events.index("type")
