"""Image-gen prompt must enforce 1920x1080 (Full HD, 16:9 landscape)."""
import asyncio
from pathlib import Path

import video_agent.browser_worker.drivers.chatgpt_image as chatgpt_image
from video_agent.browser_worker.drivers.chatgpt_image import (
    IMAGE_GEN_INSTRUCTION,
    ChatGPTImageDriver,
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

    async def start_temporary_chat():
        raise AssertionError("image generation must not use temporary chat")

    async def create_project(name):
        raise AssertionError("image generation must use a normal chat, not a Project")

    async def start_new_chat():
        events.append("new-chat")

    async def focus_composer():
        events.append("focus")
        return object()

    async def select_mode():
        events.append("select-mode")

    async def fake_fill(composer, text):
        events.append("fill")

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

    async def delete_current_chat():
        events.append("delete-chat")

    monkeypatch.setattr(driver, "_start_temporary_chat", start_temporary_chat)
    monkeypatch.setattr(driver, "_create_project", create_project)
    monkeypatch.setattr(driver, "_start_new_chat", start_new_chat)
    monkeypatch.setattr(driver, "_focus_composer", focus_composer)
    monkeypatch.setattr(driver, "_select_create_image_mode_and_aspect_ratio", select_mode, raising=False)
    monkeypatch.setattr(driver, "_fill_composer_robust", fake_fill)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)
    monkeypatch.setattr(driver, "_click_send", click_send)
    monkeypatch.setattr(driver, "_wait_for_image", wait_for_image)
    monkeypatch.setattr(driver, "_download_image", download_image)
    monkeypatch.setattr(driver, "delete_current_chat", delete_current_chat)

    asyncio.run(driver.generate_image("make a thumbnail", project_name="p", out_path=out_path))

    assert events[:4] == ["new-chat", "select-mode", "focus", "fill"]
    assert "send" in events
    assert events[-1] == "delete-chat"


def test_generate_images_selects_create_image_mode_for_each_prompt(monkeypatch, tmp_path):
    events: list[str] = []
    driver = ChatGPTImageDriver(page=object())
    driver._opened = True

    async def start_temporary_chat():
        raise AssertionError("image generation must not use temporary chat")

    async def create_project(name):
        raise AssertionError("image generation must use a normal chat, not a Project")

    async def start_new_chat():
        events.append("new-chat")

    async def focus_composer():
        events.append("focus")
        return object()

    async def select_mode():
        events.append("select-mode")

    async def fake_fill(composer, text):
        events.append("fill")

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

    async def delete_current_chat():
        events.append("delete-chat")

    monkeypatch.setattr(driver, "_start_temporary_chat", start_temporary_chat)
    monkeypatch.setattr(driver, "_create_project", create_project)
    monkeypatch.setattr(driver, "_start_new_chat", start_new_chat)
    monkeypatch.setattr(driver, "_focus_composer", focus_composer)
    monkeypatch.setattr(driver, "_select_create_image_mode_and_aspect_ratio", select_mode, raising=False)
    monkeypatch.setattr(driver, "_fill_composer_robust", fake_fill)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)
    monkeypatch.setattr(driver, "_click_send", click_send)
    monkeypatch.setattr(driver, "_wait_for_image", wait_for_image)
    monkeypatch.setattr(driver, "_download_image", download_image)
    monkeypatch.setattr(driver, "delete_current_chat", delete_current_chat)
    asyncio.run(
        driver.generate_images(
            ["first", "second"],
            project_name="p",
            out_paths=[tmp_path / "1.png", tmp_path / "2.png"],
        )
    )

    assert events.count("select-mode") == 2
    assert events[0] == "new-chat"
    assert events.index("select-mode") < events.index("focus") < events.index("fill")
    assert events[-1] == "delete-chat"


def test_image_driver_does_not_navigate_home_twice_before_new_chat(monkeypatch):
    class FakePage:
        def __init__(self):
            self.url = ""
            self.goto_calls = []

        async def goto(self, url, **kwargs):
            self.goto_calls.append(url)
            self.url = url

        async def wait_for_timeout(self, ms):
            return None

        async def content(self):
            return "<html></html>"

    async def fake_pause(*args, **kwargs):
        return None

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)
    monkeypatch.setattr(driver, "_click_first_visible", lambda *a, **k: asyncio.sleep(0, False))

    async def run():
        await driver.open()
        await driver._start_new_chat()

    asyncio.run(run())

    assert page.goto_calls == [chatgpt_image.CHATGPT_HOME]


def test_image_mode_does_not_pause_to_probe_removed_aspect_ratio_controls(monkeypatch):
    driver = ChatGPTImageDriver(page=object())
    selector_calls = []

    async def click_first_visible(selectors, *, timeout_ms=1_500):
        selector_calls.append(selectors)
        return True

    async def click_text_exact(labels, *, timeout_ms=1_500):
        return False

    monkeypatch.setattr(driver, "_click_first_visible", click_first_visible)
    monkeypatch.setattr(driver, "_click_text_exact", click_text_exact)

    asyncio.run(driver._select_create_image_mode_and_aspect_ratio("9:16"))

    assert selector_calls == [chatgpt_image.CREATE_IMAGE_MODE_SELECTORS]


def test_fill_composer_preserves_create_image_pill(monkeypatch):
    class FakePill:
        async def count(self):
            return 1

    class FakeComposer:
        def __init__(self):
            self.fill_calls = []
            self.press_calls = []
            self.type_calls = []

        def locator(self, selector):
            assert selector == chatgpt_image.IMAGE_MODE_PILL_SELECTOR
            return FakePill()

        async def fill(self, text):
            self.fill_calls.append(text)

        async def press(self, key):
            self.press_calls.append(key)

        async def type(self, text, delay=0):
            self.type_calls.append((text, delay))

    class FakePage:
        def __init__(self):
            self.evaluate_calls = []

        async def evaluate(self, script, arg=None):
            self.evaluate_calls.append((script, arg))
            return True

    async def fake_pause(*args, **kwargs):
        return None

    page = FakePage()
    composer = FakeComposer()
    driver = ChatGPTImageDriver(page=page)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)

    asyncio.run(driver._fill_composer_robust(composer, "real\n\nscene prompt"))

    assert composer.fill_calls == []
    assert composer.press_calls == ["Shift+Meta+ArrowDown"]
    assert composer.type_calls == [(" real scene prompt", 0)]
    assert page.evaluate_calls
    assert "picture_v2" in page.evaluate_calls[0][1]["pillSelector"]


def test_build_image_gen_prompt_contradictions():
    # 1. Text overlays / Typography indicators should strip "no text overlays"
    p1 = "a dark bedroom with typography overlay"
    f1 = build_image_gen_prompt(p1)
    assert "no text overlays" not in f1
    assert "no watermark" in f1

    # 2. Watermark indicators should strip "no watermark"
    p2 = "a product photo with a subtle logo"
    f2 = build_image_gen_prompt(p2)
    assert "no watermark" not in f2
    assert "no text overlays" in f2

    # 3. Border indicators should strip "no borders" and "no padding"
    p3 = "a styled frame around a picture"
    f3 = build_image_gen_prompt(p3)
    assert "no borders" not in f3
    assert "no padding" not in f3
    assert "no text overlays" in f3
    assert "no watermark" in f3


class _FakeFileInput:
    def __init__(self, on_set):
        self._on_set = on_set

    async def set_input_files(self, path):
        self._on_set(path)


class _FakeFileInputs:
    """Fake Playwright locator for input[type='file'] with N inputs."""

    def __init__(self, n, set_log):
        self._n = n
        self._set_log = set_log

    async def count(self):
        return self._n

    def nth(self, idx):
        return _FakeFileInput(lambda p: self._set_log.append(idx))


class _FakeAttachPage:
    def __init__(self, file_inputs):
        self._file_inputs = file_inputs

    def locator(self, selector):
        assert selector == "input[type='file']"
        return self._file_inputs


def test_attach_reference_stops_after_first_input_registers(monkeypatch, tmp_path):
    """Dup-attach fix: image fed one-at-a-time; stop once preview registers.

    Guards against feeding the reference to EVERY file input (which doubled the
    reference chip on the active composer input)."""
    ref = tmp_path / "persona.png"
    ref.write_bytes(b"fake ref image")

    set_log: list[int] = []
    file_inputs = _FakeFileInputs(n=3, set_log=set_log)
    driver = ChatGPTImageDriver(page=_FakeAttachPage(file_inputs))

    # preview count: 0 before any set; jumps to 1 right after the first set.
    async def preview_count():
        return 1 if set_log else 0

    async def click_first_visible(selectors, *, timeout_ms=1500):
        return True

    async def fake_pause(*args, **kwargs):
        return None

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(driver, "_attachment_preview_count", preview_count)
    monkeypatch.setattr(driver, "_click_first_visible", click_first_visible)
    monkeypatch.setattr(chatgpt_image, "human_pause", fake_pause)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    asyncio.run(driver._attach_reference_image(ref))

    # Only the FIRST input was fed — no doubled attach across the other inputs.
    assert set_log == [0]
