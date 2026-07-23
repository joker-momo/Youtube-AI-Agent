"""bridge 20260722 (bug-511 recurrence 4): Gemini serves the response image as a
blob:/data: URL. Detection must accept it; persistence must fetch the ORIGINAL
bytes from inside the page and validate them (magic + MIME + decode + size)
before writing; and a client disconnect must cancel the in-flight generation.
"""
from __future__ import annotations

import asyncio
import base64
import io

import pytest

from video_agent.browser_worker import app
from video_agent.browser_worker.drivers.base import BrowserDriverError
from video_agent.browser_worker.drivers.gemini_image import GeminiImageDriver


def _make_png(seed: int = 7, size: int = 64) -> bytes:
    from PIL import Image
    rng = bytes((seed * 31 + i * 17) % 256 for i in range(size * size * 3))
    buf = io.BytesIO()
    Image.frombytes("RGB", (size, size), rng).save(buf, "PNG")
    return buf.getvalue()


_ORIGINAL_PNG = _make_png()
_BLOB_SRC = "blob:https://gemini.google.com/33819c48-4433-aec7-scene27"


class _BlobPage:
    """Fake Page returning the STRUCTURED blob-fetch result the driver now expects."""

    def __init__(self, body: bytes = _ORIGINAL_PNG, *, src: str = _BLOB_SRC,
                 ok: bool = True, status: int = 200, content_type: str = "image/png",
                 size: int | None = None):
        self._body, self._src = body, src
        self._ok, self._status, self._ct = ok, status, content_type
        self._size = size if size is not None else len(body)
        self.evaluations: list[str] = []

    async def evaluate(self, js: str, *args):
        self.evaluations.append(js)
        if "walk(document" in js:
            return [{"src": self._src, "w": 708, "h": 395}]
        if "arrayBuffer" in js:
            return {"ok": self._ok, "status": self._status, "contentType": self._ct,
                    "size": self._size, "b64": base64.b64encode(self._body).decode("ascii")}
        raise AssertionError(js[:40])

    async def wait_for_timeout(self, _ms):
        pass


def _run(driver, src, dest):
    return asyncio.run(driver._download_image(src, dest))


# ── detection + happy path ────────────────────────────────────────────────────
def test_find_response_image_accepts_blob_src():
    assert asyncio.run(GeminiImageDriver(_BlobPage())._find_response_image_src()) == _BLOB_SRC


def test_valid_blob_saves_original_bytes_atomically(tmp_path):
    page = _BlobPage()
    dest = tmp_path / "assets" / "scene-27-regenerated.png"
    _run(GeminiImageDriver(page), _BLOB_SRC, dest)
    assert dest.read_bytes() == _ORIGINAL_PNG
    assert any("arrayBuffer" in e for e in page.evaluations)
    assert list(dest.parent.iterdir()) == [dest]  # no temp file


# ── blob failure modes ────────────────────────────────────────────────────────
def test_blob_fetch_non_2xx_is_rejected(tmp_path):
    driver = GeminiImageDriver(_BlobPage(ok=False, status=500))
    with pytest.raises(BrowserDriverError, match="not ok"):
        _run(driver, _BLOB_SRC, tmp_path / "s.png")


def test_blob_oversize_is_rejected_before_decode(tmp_path):
    page = _BlobPage(size=GeminiImageDriver._MAX_IMAGE_BYTES + 1)
    with pytest.raises(BrowserDriverError, match="size cap"):
        _run(GeminiImageDriver(page), _BLOB_SRC, tmp_path / "s.png")


def test_non_image_blob_bytes_rejected_not_saved(tmp_path):
    html = b"<html><body>error: quota exceeded. " + b"padding " * 30 + b"</body></html>"
    page = _BlobPage(body=html, content_type="text/html")
    dest = tmp_path / "s.png"
    with pytest.raises(BrowserDriverError, match="non-image type|magic bytes"):
        _run(GeminiImageDriver(page), _BLOB_SRC, dest)
    assert not dest.exists()


def test_truncated_tiny_blob_rejected(tmp_path):
    with pytest.raises(BrowserDriverError, match="too small"):
        _run(GeminiImageDriver(_BlobPage(body=b"\x89PNG")), _BLOB_SRC, tmp_path / "s.png")


def test_declared_mime_mismatch_rejected(tmp_path):
    # bytes are PNG but the server declared JPEG.
    page = _BlobPage(content_type="image/jpeg")
    with pytest.raises(BrowserDriverError, match="MIME mismatch"):
        _run(GeminiImageDriver(page), _BLOB_SRC, tmp_path / "s.png")


def test_corrupt_but_magic_valid_image_rejected_on_decode(tmp_path):
    corrupt = b"\x89PNG\r\n\x1a\n" + b"\x00" * 300  # valid magic, undecodable
    page = _BlobPage(body=corrupt, content_type="image/png")
    dest = tmp_path / "s.png"
    with pytest.raises(BrowserDriverError, match="failed to decode"):
        _run(GeminiImageDriver(page), _BLOB_SRC, dest)
    assert not dest.exists()


def test_malformed_base64_from_blob_rejected(tmp_path):
    class _BadB64Page(_BlobPage):
        async def evaluate(self, js, *args):
            if "arrayBuffer" in js:
                return {"ok": True, "status": 200, "contentType": "image/png",
                        "size": 500, "b64": "!!!not base64!!!"}
            return [{"src": self._src, "w": 708, "h": 395}]

    with pytest.raises(BrowserDriverError, match="Malformed base64"):
        _run(GeminiImageDriver(_BadB64Page()), _BLOB_SRC, tmp_path / "s.png")


# ── data: URI path ────────────────────────────────────────────────────────────
def test_data_uri_image_is_parsed_and_saved(tmp_path):
    src = "data:image/png;base64," + base64.b64encode(_ORIGINAL_PNG).decode("ascii")
    dest = tmp_path / "d.png"
    page = _BlobPage()  # evaluate should NOT be called for a data: URI
    _run(GeminiImageDriver(page), src, dest)
    assert dest.read_bytes() == _ORIGINAL_PNG
    assert page.evaluations == [], "data: URI must be parsed in Python, not via the page"


def test_data_uri_malformed_base64_rejected(tmp_path):
    src = "data:image/png;base64,%%%notb64%%%"
    with pytest.raises(BrowserDriverError, match="Malformed base64"):
        _run(GeminiImageDriver(_BlobPage()), src, tmp_path / "d.png")


def test_data_uri_non_image_mime_rejected(tmp_path):
    src = "data:text/html;base64," + base64.b64encode(b"x" * 200).decode("ascii")
    with pytest.raises(BrowserDriverError, match="non-image type"):
        _run(GeminiImageDriver(_BlobPage()), src, tmp_path / "d.png")


# ── http path unchanged ───────────────────────────────────────────────────────
def test_http_src_uses_request_api_with_content_type(tmp_path):
    class _Resp:
        status = 200
        headers = {"content-type": "image/png"}
        async def body(self):
            return _ORIGINAL_PNG

    class _Req:
        async def get(self, src):
            return _Resp()

    class _Ctx:
        request = _Req()

    class _HttpPage:
        context = _Ctx()
        def __init__(self):
            self.evaluations = []
        async def evaluate(self, js, *a):
            self.evaluations.append(js)
            return {}
        async def wait_for_timeout(self, _ms):
            pass

    page = _HttpPage()
    dest = tmp_path / "h.png"
    _run(GeminiImageDriver(page), "https://cdn.example/img.png", dest)
    assert dest.read_bytes() == _ORIGINAL_PNG
    assert page.evaluations == []


# ── bounded fetch ─────────────────────────────────────────────────────────────
def test_blob_fetch_is_bounded_and_does_not_hang(tmp_path):
    class _HangPage(_BlobPage):
        async def evaluate(self, js, *args):
            if "arrayBuffer" in js:
                await asyncio.sleep(3600)
            return [{"src": self._src, "w": 708, "h": 395}]

    driver = GeminiImageDriver(_HangPage())
    driver._BLOB_FETCH_TIMEOUT_SEC = 0.2
    with pytest.raises(BrowserDriverError):
        _run(driver, _BLOB_SRC, tmp_path / "h.png")


# ── disconnect-aware cancellation ─────────────────────────────────────────────
class _FakeRequest:
    def __init__(self, disconnect_after: int):
        self._n = 0
        self._after = disconnect_after
    async def is_disconnected(self):
        self._n += 1
        return self._n > self._after


def test_disconnect_guard_cancels_generation_and_runs_cleanup():
    cleaned = {"done": False}

    async def slow_impl():
        try:
            await asyncio.sleep(30)  # long generation
            return {"ok": True}
        finally:
            cleaned["done"] = True  # browser/page cleanup runs on cancel

    async def scenario():
        app._DISCONNECT_POLL_SEC = 0.01
        with pytest.raises(app.HTTPException) as ei:
            await app._run_with_disconnect_guard(_FakeRequest(disconnect_after=0), slow_impl())
        assert ei.value.status_code == 499
        assert cleaned["done"], "cancelled generation must run its cleanup finally"

    asyncio.run(scenario())


def test_disconnect_guard_returns_result_when_connected():
    async def quick_impl():
        return {"ok": True, "bytes": 6}

    async def scenario():
        res = await app._run_with_disconnect_guard(_FakeRequest(disconnect_after=10_000), quick_impl())
        assert res == {"ok": True, "bytes": 6}

    asyncio.run(scenario())


def test_image_routes_accept_request_for_disconnect_awareness():
    import inspect
    for fn in (app.chatgpt_image, app.chatgpt_image_batch):
        params = inspect.signature(fn).parameters
        assert "request" in params, f"{fn.__name__} must accept request for disconnect handling"
