"""Ensure video_agent.providers exposes MockProvider + all interfaces/adapters."""

from __future__ import annotations


def test_provider_exports_include_mock_provider():
    from video_agent.providers import MockProvider

    assert MockProvider is not None


def test_provider_exports_include_interfaces():
    from video_agent.providers import (
        ImageProvider,
        LLMProvider,
        Renderer,
        TTSProvider,
    )

    for cls in (ImageProvider, LLMProvider, Renderer, TTSProvider):
        assert cls is not None


def test_provider_exports_include_browser_client_adapters():
    from video_agent.providers import (
        BrowserClientImageProvider,
        BrowserClientLLMProvider,
    )

    for cls in (
        BrowserClientImageProvider,
        BrowserClientLLMProvider,
    ):
        assert cls is not None


def test_provider_all_lists_mock_provider():
    import video_agent.providers as providers_module

    assert "MockProvider" in providers_module.__all__
    assert "ImageProvider" in providers_module.__all__
