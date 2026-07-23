"""bridge 20260722 (bug-511 recurrence 4): the Gemini response image is served
as a blob: URL. Detection must accept it, and persistence must fetch the
ORIGINAL-resolution bytes from inside the page (not screenshot the shrunk <img>).
"""
from __future__ import annotations

import asyncio
import base64

from video_agent.browser_worker.drivers.gemini_image import GeminiImageDriver


def _make_png(seed: int = 7) -> bytes:
    """A valid PNG comfortably larger than the driver's minimum image size
    (noise so it does not compress down to a few bytes)."""
    import io

    from PIL import Image
    rng = bytes((seed * 31 + i * 17) % 256 for i in range(64 * 64 * 3))
    buf = io.BytesIO()
    Image.frombytes("RGB", (64, 64), rng).save(buf, "PNG")
    return buf.getvalue()


_ORIGINAL_PNG = _make_png()
_BLOB_SRC = "blob:https://gemini.google.com/33819c48-4433-aec7-scene27"


class _BlobPage:
    """Fake Playwright Page: exposes one blob-src response image (708x395 CSS box,
    as in the live evidence) and serves its ORIGINAL bytes via an in-page fetch."""

    def __init__(self, original: bytes, src: str):
        self._original = original
        self._src = src
        self.evaluations: list[str] = []

    async def evaluate(self, js: str, *args):
        self.evaluations.append(js)
        if "walk(document" in js:  # _FIND_IMAGE_JS
            return [{"src": self._src, "w": 708, "h": 395}]
        if "arrayBuffer" in js:    # _READ_BLOB_JS — arg is the blob src
            assert args and args[0] == self._src
            return base64.b64encode(self._original).decode("ascii")
        raise AssertionError(f"unexpected evaluate: {js[:40]}")

    async def wait_for_timeout(self, _ms):
        pass


def test_find_response_image_accepts_blob_src(tmp_path):
    page = _BlobPage(_ORIGINAL_PNG, _BLOB_SRC)
    driver = GeminiImageDriver(page)
    src = asyncio.run(driver._find_response_image_src())
    assert src == _BLOB_SRC, "blob-src response image was not detected"


def test_download_saves_original_blob_bytes_atomically(tmp_path):
    page = _BlobPage(_ORIGINAL_PNG, _BLOB_SRC)
    driver = GeminiImageDriver(page)
    dest = tmp_path / "assets" / "scene-27-regenerated.png"
    out = asyncio.run(driver._download_image(_BLOB_SRC, dest))
    assert out == dest and dest.exists()
    # EXACT original bytes — proves we fetched the blob, did not screenshot.
    assert dest.read_bytes() == _ORIGINAL_PNG
    # It went through the in-page fetch path, not the request API.
    assert any("arrayBuffer" in e for e in page.evaluations)
    # No stray temp file left by the atomic writer.
    assert list(dest.parent.iterdir()) == [dest]


def test_http_src_still_uses_request_api_not_page_fetch(tmp_path):
    """Regression guard: an ordinary http image must NOT go through the in-page
    blob path — it uses the browser request context as before."""
    class _HttpResp:
        status = 200
        async def body(self):
            return _ORIGINAL_PNG

    class _Req:
        async def get(self, src):
            return _HttpResp()

    class _Ctx:
        request = _Req()

    class _HttpPage:
        context = _Ctx()
        def __init__(self):
            self.evaluations = []
        async def evaluate(self, js, *a):
            self.evaluations.append(js)
            return "should-not-be-called"
        async def wait_for_timeout(self, _ms):
            pass

    page = _HttpPage()
    driver = GeminiImageDriver(page)
    dest = tmp_path / "http.png"
    asyncio.run(driver._download_image("https://cdn.example/img.png", dest))
    assert dest.read_bytes() == _ORIGINAL_PNG
    assert page.evaluations == [], "http src must not trigger the in-page blob fetch"


def test_non_image_blob_bytes_are_rejected_not_saved(tmp_path):
    """bridge 20260722 r2: a blob that fetches to a non-image (HTML error page,
    garbage, truncation) must NOT be written as the generated poster."""
    import pytest

    from video_agent.browser_worker.drivers.base import BrowserDriverError

    class _GarbagePage(_BlobPage):
        async def evaluate(self, js, *args):
            self.evaluations.append(js)
            if "walk(document" in js:
                return [{"src": self._src, "w": 708, "h": 395}]
            if "arrayBuffer" in js:
                html = b"<html><body>error: quota exceeded. " + b"padding " * 20 + b"</body></html>"
                return base64.b64encode(html).decode("ascii")  # >100 bytes, not an image
            raise AssertionError(js[:40])

    page = _GarbagePage(_ORIGINAL_PNG, _BLOB_SRC)
    driver = GeminiImageDriver(page)
    dest = tmp_path / "scene.png"
    with pytest.raises(BrowserDriverError, match="magic bytes"):
        asyncio.run(driver._download_image(_BLOB_SRC, dest))
    assert not dest.exists(), "a non-image was persisted"


def test_truncated_tiny_blob_is_rejected(tmp_path):
    import pytest

    from video_agent.browser_worker.drivers.base import BrowserDriverError

    class _TinyPage(_BlobPage):
        async def evaluate(self, js, *args):
            if "arrayBuffer" in js:
                return base64.b64encode(b"\x89PNG").decode("ascii")  # 4 bytes, truncated
            return [{"src": self._src, "w": 708, "h": 395}]

    driver = GeminiImageDriver(_TinyPage(_ORIGINAL_PNG, _BLOB_SRC))
    with pytest.raises(BrowserDriverError, match="too small"):
        asyncio.run(driver._download_image(_BLOB_SRC, tmp_path / "s.png"))


def test_valid_image_is_validated_and_saved(tmp_path):
    """The happy path still writes a real, magic-valid image."""
    page = _BlobPage(_ORIGINAL_PNG, _BLOB_SRC)
    driver = GeminiImageDriver(page)
    dest = tmp_path / "ok.png"
    asyncio.run(driver._download_image(_BLOB_SRC, dest))
    saved = dest.read_bytes()
    assert saved == _ORIGINAL_PNG and saved[:8] == b"\x89PNG\r\n\x1a\n" and len(saved) >= 100


def test_blob_fetch_is_bounded_and_does_not_hang(tmp_path):
    """bridge 20260722 r2: a hung in-page fetch must be bounded, not block forever."""
    import pytest

    from video_agent.browser_worker.drivers.base import BrowserDriverError

    class _HangPage(_BlobPage):
        async def evaluate(self, js, *args):
            if "arrayBuffer" in js:
                await asyncio.sleep(3600)  # would hang forever without the bound
            return [{"src": self._src, "w": 708, "h": 395}]

    driver = GeminiImageDriver(_HangPage(_ORIGINAL_PNG, _BLOB_SRC))
    driver._BLOB_FETCH_TIMEOUT_SEC = 0.2  # shrink the bound for the test
    with pytest.raises(BrowserDriverError):
        asyncio.run(driver._download_image(_BLOB_SRC, tmp_path / "h.png"))
