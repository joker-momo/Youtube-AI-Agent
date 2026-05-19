import wave
from pathlib import Path

from video_agent.stages.assets import prepare_assets


def scene_doc():
    return {
        "total_duration_sec": 4,
        "scenes": [
            {
                "id": "scene-01",
                "duration_sec": 2,
                "narration": "Primera escena con voz natural.",
                "visual_prompt": "warm kitchen",
                "on_screen_text": "Primera escena",
                "caption": "Primera escena con voz natural.",
                "motion": "slow_zoom",
                "asset_refs": {"background": "assets/scene-01.jpg"},
            },
            {
                "id": "scene-02",
                "duration_sec": 2,
                "narration": "Segunda escena con una pausa breve.",
                "visual_prompt": "calm bedroom",
                "on_screen_text": "Segunda escena",
                "caption": "Segunda escena con una pausa breve.",
                "motion": "pan_left",
                "asset_refs": {"background": "assets/scene-02.jpg"},
            },
        ],
    }


def style_dna():
    return {
        "palette": {
            "background": "#F6F1E8",
            "primary": "#5E8C6A",
            "secondary": "#D6A85A",
            "accent": "#F2C94C",
            "text": "#26332F",
        }
    }


def write_wav(path: Path, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * sample_rate)


class FakeTTSClient:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, output_path, config):
        self.calls.append((text, output_path, config))
        write_wav(output_path)
        return {"provider": "kokoro", "voice_id": config["voice_id"], "sample_rate": 24000}


def test_prepare_assets_uses_real_tts_provider_when_configured(tmp_path):
    client = FakeTTSClient()

    manifest = prepare_assets(
        tmp_path / "job-tts",
        style_dna(),
        scene_doc(),
        tts_config={"provider": "kokoro", "voice_id": "ef_dora", "lang_code": "e"},
        tts_client=client,
    )

    assert len(client.calls) == 2
    assert "Primera escena con voz natural." in client.calls[0][0]
    assert "Segunda escena con una pausa breve." in client.calls[1][0]
    assert manifest["audio"]["provider"] == "kokoro"
    assert manifest["audio"]["voice_id"] == "ef_dora"
    assert manifest["audio"]["source"] == "tts"
    assert (tmp_path / "job-tts/assets/narration.wav").exists()
    with wave.open(str(tmp_path / "job-tts/assets/narration.wav"), "r") as handle:
        assert handle.getframerate() == 24000
        assert handle.getnframes() == 4 * 24000


def test_prepare_assets_keeps_silent_mock_audio_by_default(tmp_path):
    manifest = prepare_assets(tmp_path / "job-mock-tts", style_dna(), scene_doc())

    assert manifest["audio"]["provider"] == "mock-local"
    assert manifest["audio"]["source"] == "silent_placeholder"
    assert (tmp_path / "job-mock-tts/assets/narration.wav").exists()
