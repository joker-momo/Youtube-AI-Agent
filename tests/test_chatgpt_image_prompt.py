"""Image-gen prompt must enforce 1920x1080 (Full HD, 16:9 landscape)."""
import asyncio
import base64
import io
from pathlib import Path

import pytest

import video_agent.browser_worker.drivers.chatgpt_image as chatgpt_image
from video_agent.browser_worker.drivers.chatgpt_image import (
    IMAGE_GEN_INSTRUCTION,
    ChatGPTImageDriver,
    build_image_gen_prompt,
)


def _make_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(20, 40, 60)).save(buffer, "PNG")
    return buffer.getvalue()


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


def test_click_send_accepts_stop_button_when_prompt_auto_submitted(monkeypatch):
    class FakeLocator:
        def __init__(self, visible: bool):
            self.first = self
            self._visible = visible

        async def is_visible(self, timeout):
            return self._visible

        async def count(self):
            return 1

    class FakePage:
        def locator(self, selector):
            return FakeLocator(selector in chatgpt_image.STOP_BUTTON_SELECTORS)

    async def fail_screenshot(*args, **kwargs):
        raise AssertionError("already-submitted prompts must not be treated as failures")

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)
    monkeypatch.setattr(chatgpt_image, "save_trace_screenshot", fail_screenshot)

    asyncio.run(driver._click_send(before_user_turns=0))


@pytest.mark.parametrize(
    "assistant_text",
    [
        (
            "Vui lòng tải lên hoặc chọn lại hình ảnh gốc cần chỉnh sửa. "
            "Hiện tại hệ thống đang nhận yêu cầu này như một tác vụ chỉnh sửa ảnh "
            "nhưng không có ảnh nguồn khả dụng để tôi thực hiện."
        ),
        "Please upload or select the original image you want me to edit.",
        "Please upload the image you'd like me to edit.",
        "Sube o selecciona la imagen original que quieres editar.",
        "Necesito que subas la imagen que quieres editar.",
        "Vui lòng tải lên ảnh bạn muốn chỉnh sửa.",
    ],
)
def test_wait_for_image_fails_fast_when_chatgpt_requests_a_source_image(
    monkeypatch, assistant_text
):
    class FakePage:
        def __init__(self):
            self.wait_calls = 0

        async def evaluate(self, script, arg=None):
            if "const containers" in script:
                return ""
            if "assistantMessages" in script:
                return assistant_text
            return False

        async def wait_for_timeout(self, _ms):
            self.wait_calls += 1

    async def fake_screenshot(*args, **kwargs):
        return "/tmp/source-image-required.png"

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)
    monkeypatch.setattr(chatgpt_image, "save_trace_screenshot", fake_screenshot)

    with pytest.raises(chatgpt_image.BrowserDriverError, match="source image"):
        asyncio.run(driver._wait_for_image(10))

    assert page.wait_calls == 0


def test_wait_for_image_does_not_misclassify_benign_assistant_status(monkeypatch):
    class FakePage:
        async def evaluate(self, script, arg=None):
            if "const containers" in script:
                return ""
            if "assistantMessages" in script:
                return "I am generating the requested image now."
            return False

        async def wait_for_timeout(self, _ms):
            pass

    async def fake_screenshot(*args, **kwargs):
        return "/tmp/timeout.png"

    driver = ChatGPTImageDriver(page=FakePage())
    monkeypatch.setattr(chatgpt_image, "save_trace_screenshot", fake_screenshot)

    with pytest.raises(chatgpt_image.BrowserDriverError, match="timed out"):
        asyncio.run(driver._wait_for_image(10))


def test_wait_for_image_ignores_source_request_from_an_older_assistant_turn(
    monkeypatch,
):
    stale_refusal = "Please upload the original image you want me to edit."

    class FakePage:
        async def evaluate(self, script, arg=None):
            if "const containers" in script:
                return ""
            if "assistantMessages" in script:
                return "" if arg == 1 else stale_refusal
            return False

        async def wait_for_timeout(self, _ms):
            pass

    async def fake_screenshot(*args, **kwargs):
        return "/tmp/timeout.png"

    driver = ChatGPTImageDriver(page=FakePage())
    driver._assistant_turn_floor = 1
    monkeypatch.setattr(chatgpt_image, "save_trace_screenshot", fake_screenshot)

    with pytest.raises(chatgpt_image.BrowserDriverError, match="timed out"):
        asyncio.run(driver._wait_for_image(10))


def test_find_response_image_src_accepts_generated_blob_and_skips_user_attachment():
    generated = "blob:https://chatgpt.com/generated-image"

    class FakePage:
        async def evaluate(self, _script, _exclude_urls=None):
            return [
                {
                    "src": "blob:https://chatgpt.com/user-attachment",
                    "alt": "",
                    "role": "user",
                    "width": 480,
                    "height": 270,
                },
                {
                    "src": generated,
                    "alt": "Generated image 1",
                    "role": "",
                    "width": 480,
                    "height": 270,
                },
            ]

    assert (
        asyncio.run(
            ChatGPTImageDriver(FakePage())._find_response_image_src([])
        )
        == generated
    )


def test_download_image_persists_chatgpt_blob_bytes(tmp_path):
    original = _make_png()
    source = "blob:https://chatgpt.com/generated-image"
    destination = tmp_path / "scene.png"

    class FakePage:
        async def evaluate(self, script, args):
            assert "fetch(src)" in script
            assert args[0] == source
            return {
                "ok": True,
                "status": 200,
                "contentType": "image/png",
                "size": len(original),
                "b64": base64.b64encode(original).decode("ascii"),
            }

        async def wait_for_timeout(self, _milliseconds):
            pass

    asyncio.run(ChatGPTImageDriver(FakePage())._download_image(source, destination))

    assert destination.read_bytes() == original


def test_download_image_rejects_oversized_http_response_before_reading_body(tmp_path):
    class Response:
        status = 200
        headers = {
            "content-type": "image/png",
            "content-length": str(ChatGPTImageDriver._MAX_IMAGE_BYTES + 1),
        }

        async def body(self):
            raise AssertionError("oversized HTTP response must not be buffered")

    class Request:
        async def get(self, _source):
            return Response()

    class Context:
        request = Request()

    class FakePage:
        context = Context()

        async def wait_for_timeout(self, _milliseconds):
            pass

    with pytest.raises(chatgpt_image.BrowserDriverError, match="size cap"):
        asyncio.run(
            ChatGPTImageDriver(FakePage())._download_image(
                "https://cdn.example/generated.png", tmp_path / "scene.png"
            )
        )


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

    async def click_send(*args, **kwargs):
        events.append("send")

    async def wait_for_image(timeout, exclude_urls=None):
        events.append("wait")
        return "https://example.com/image.png"

    async def download_image(src, dest):
        events.append("download")
        Path(dest).write_bytes(b"fake image")

    async def delete_current_chat():
        events.append("delete-chat")
        return {
            "kind": "conversation",
            "status": "deleted",
            "conversation_id": "conversation-123",
            "attempts": 1,
            "verified": True,
        }

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

    async def click_send(*args, **kwargs):
        events.append("send")

    async def wait_for_image(timeout, exclude_urls=None):
        events.append("wait")
        return f"https://example.com/image-{events.count('wait')}.png"

    async def download_image(src, dest):
        events.append("download")
        Path(dest).write_bytes(b"fake image")

    async def delete_current_chat():
        events.append("delete-chat")
        return {
            "kind": "conversation",
            "status": "deleted",
            "conversation_id": "conversation-123",
            "attempts": 1,
            "verified": True,
        }

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
    results = asyncio.run(
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
    assert [item["cleanup"]["status"] for item in results] == ["deleted", "deleted"]


class _CleanupActionLocator:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    async def count(self):
        return 1

    async def is_visible(self, **_kwargs):
        if self.name == "delete-menu":
            self.page.menu_checks += 1
            return next(self.page.menu_visibility)
        if self.name == "delete-confirmation":
            self.page.confirmation_checks += 1
            return next(self.page.confirmation_visibility)
        return True

    async def click(self, **_kwargs):
        self.page.clicked.append(self.name)

    def locator(self, selector):
        self.page.selectors.append(selector)
        if self.name == "link":
            return self.page.row
        if self.name == "row":
            return self.page.actions
        raise AssertionError(f"unexpected nested locator on {self.name}: {selector}")

    def get_by_role(self, role, **_kwargs):
        if self.name == "dialog" and role == "button":
            return self.page.confirmation
        raise AssertionError(f"unexpected nested role query on {self.name}: {role}")


class _CleanupActionPage:
    def __init__(self, *, menu_visibility=(True,), confirmation_visibility=(True,)):
        self.clicked: list[str] = []
        self.selectors: list[str] = []
        self.menu_checks = 0
        self.confirmation_checks = 0
        self.menu_visibility = iter(menu_visibility)
        self.confirmation_visibility = iter(confirmation_visibility)
        self.link = _CleanupActionLocator(self, "link")
        self.row = _CleanupActionLocator(self, "row")
        self.actions = _CleanupActionLocator(self, "chat-actions")
        self.menu = _CleanupActionLocator(self, "delete-menu")
        self.dialog = _CleanupActionLocator(self, "dialog")
        self.confirmation = _CleanupActionLocator(self, "delete-confirmation")

    def locator(self, selector):
        self.selectors.append(selector)
        if selector.startswith("a[href*='/c/"):
            return self.link
        if selector == "div[role='dialog'], div[role='alertdialog']":
            return self.dialog
        raise AssertionError(f"unexpected page locator: {selector}")

    def get_by_role(self, role, **_kwargs):
        if role == "menuitem":
            return self.menu
        raise AssertionError(f"unexpected page role query: {role}")


def test_delete_current_chat_retries_until_sidebar_confirms_removal(monkeypatch):
    """A clicked confirmation is not success until the conversation is absent."""
    class FakePage(_CleanupActionPage):
        def __init__(self):
            super().__init__()
            self.reload_calls = 0

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def reload(self, **_kwargs):
            self.reload_calls += 1

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)
    attempts: list[str] = []
    visible_states = iter([True, True, True, False, False, False])
    presence_checks = 0

    async def stable_conversation_id():
        return "conversation-123"

    async def delete_once(conversation_id: str):
        attempts.append(conversation_id)
        return {"status": "confirmed"}

    async def conversation_is_present(_conversation_id: str):
        nonlocal presence_checks
        presence_checks += 1
        return next(visible_states)

    server_checks = 0

    async def conversation_is_deleted_server_side(_conversation_id: str):
        nonlocal server_checks
        server_checks += 1
        return True

    sidebar_removals = 0

    async def remove_conversation_from_sidebar(_conversation_id: str):
        nonlocal sidebar_removals
        sidebar_removals += 1
        return True

    async def no_pause(*_args, **_kwargs):
        return None

    monkeypatch.setattr(driver, "_stable_conversation_id", stable_conversation_id, raising=False)
    monkeypatch.setattr(driver, "_delete_chat_once", delete_once, raising=False)
    monkeypatch.setattr(driver, "_conversation_is_present", conversation_is_present, raising=False)
    monkeypatch.setattr(
        driver,
        "_conversation_is_deleted_server_side",
        conversation_is_deleted_server_side,
        raising=False,
    )
    monkeypatch.setattr(
        driver,
        "_remove_conversation_from_sidebar",
        remove_conversation_from_sidebar,
        raising=False,
    )
    monkeypatch.setattr(chatgpt_image, "human_pause", no_pause)

    status = asyncio.run(driver.delete_current_chat())

    assert status == {
        "kind": "conversation",
        "status": "deleted",
        "conversation_id": "conversation-123",
        "attempts": 2,
        "verified": True,
    }
    assert attempts == ["conversation-123", "conversation-123"]
    assert page.reload_calls == 2
    assert server_checks == 1
    assert sidebar_removals == 1
    assert presence_checks == 6


def test_delete_chat_once_supports_current_chat_actions_control():
    """Cleanup must find ChatGPT's current row-local action button."""
    class FakePage(_CleanupActionPage):
        async def wait_for_timeout(self, _milliseconds):
            return None

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)

    outcome = asyncio.run(driver._delete_chat_once("conversation-123"))

    assert outcome == {"status": "confirmed", "reason": None}
    assert page.clicked == ["chat-actions", "delete-menu", "delete-confirmation"]
    assert "xpath=ancestor::*[@role='group' or self::li][1]" in page.selectors
    assert any("aria-label='Chat actions'" in selector for selector in page.selectors)


def test_delete_chat_once_waits_for_late_chat_actions_menu():
    """ChatGPT can render the action menu after the first post-click poll."""
    class FakePage(_CleanupActionPage):
        def __init__(self):
            super().__init__(menu_visibility=(False, True))

        async def wait_for_timeout(self, _milliseconds):
            return None

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)

    outcome = asyncio.run(driver._delete_chat_once("conversation-123"))

    assert outcome == {"status": "confirmed", "reason": None}
    assert page.menu_checks == 2


def test_delete_chat_once_waits_for_late_delete_confirmation():
    """A visible Delete menu item can precede a delayed confirmation dialog."""
    class FakePage(_CleanupActionPage):
        def __init__(self):
            super().__init__(confirmation_visibility=(False, True))

        async def wait_for_timeout(self, _milliseconds):
            return None

    page = FakePage()
    driver = ChatGPTImageDriver(page=page)

    outcome = asyncio.run(driver._delete_chat_once("conversation-123"))

    assert outcome == {"status": "confirmed", "reason": None}
    assert page.confirmation_checks == 2


def test_delete_current_chat_reports_unavailable_conversation_id(monkeypatch):
    driver = ChatGPTImageDriver(page=object())

    async def stable_conversation_id():
        return None

    monkeypatch.setattr(driver, "_stable_conversation_id", stable_conversation_id, raising=False)

    status = asyncio.run(driver.delete_current_chat())

    assert status == {
        "kind": "conversation",
        "status": "failed",
        "conversation_id": None,
        "attempts": 0,
        "verified": False,
        "reason": "conversation_id_unavailable",
    }


def test_generate_image_returns_cleanup_failure_in_successful_image_response(monkeypatch, tmp_path):
    """A saved image must not hide that its normal ChatGPT chat remains."""
    out_path = tmp_path / "thumbnail.png"
    driver = ChatGPTImageDriver(page=object())
    driver._opened = True

    async def ensure_image_session(_project_name):
        return None

    async def count_turns():
        return 0

    async def select_mode(*_args, **_kwargs):
        return None

    async def focus_composer():
        return object()

    async def fill(*_args, **_kwargs):
        return None

    async def click_send(*_args, **_kwargs):
        return None

    async def wait_for_image(*_args, **_kwargs):
        return "https://example.com/image.png"

    async def download_image(*_args, **_kwargs):
        out_path.write_bytes(b"image")

    async def teardown(_project_name):
        return {
            "kind": "conversation",
            "status": "failed",
            "conversation_id": "conversation-123",
            "attempts": 3,
            "verified": False,
            "reason": "sidebar_entry_still_present",
        }

    async def no_pause(*_args, **_kwargs):
        return None

    monkeypatch.setattr(driver, "_ensure_image_session", ensure_image_session)
    monkeypatch.setattr(driver, "_user_turn_count", count_turns)
    monkeypatch.setattr(driver, "_assistant_turn_count", count_turns)
    monkeypatch.setattr(driver, "_select_create_image_mode_and_aspect_ratio", select_mode)
    monkeypatch.setattr(driver, "_focus_composer", focus_composer)
    monkeypatch.setattr(driver, "_fill_composer_robust", fill)
    monkeypatch.setattr(driver, "_click_send", click_send)
    monkeypatch.setattr(driver, "_wait_for_image", wait_for_image)
    monkeypatch.setattr(driver, "_download_image", download_image)
    monkeypatch.setattr(driver, "_teardown_image_session", teardown)
    monkeypatch.setattr(chatgpt_image, "human_pause", no_pause)

    result = asyncio.run(
        driver.generate_image("make a thumbnail", project_name="p", out_path=out_path)
    )

    assert result["bytes"] == 5
    assert result["cleanup"]["status"] == "failed"
    assert result["cleanup"]["reason"] == "sidebar_entry_still_present"


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
