import hashlib
import io
import math
import struct
import wave

from video_agent.shorts.original_bgm import build_original_bgm_wav


def test_original_bgm_is_deterministic_and_has_no_external_audio_inputs():
    wav_a, metadata_a = build_original_bgm_wav(
        duration_sec=1.0,
        seed_key="short-01-cafe-sin-azucar",
    )
    wav_b, metadata_b = build_original_bgm_wav(
        duration_sec=1.0,
        seed_key="short-01-cafe-sin-azucar",
    )

    assert wav_a == wav_b
    assert metadata_a == metadata_b
    assert metadata_a["source"] == "procedural_synthesis"
    assert metadata_a["external_audio_inputs"] == []
    assert metadata_a["copyright_basis"] == "original_algorithmic_composition"


def test_original_bgm_seed_changes_the_composition_and_wav_contract():
    wav_a, _ = build_original_bgm_wav(duration_sec=1.0, seed_key="short-a")
    wav_b, metadata = build_original_bgm_wav(duration_sec=1.0, seed_key="short-b")

    assert hashlib.sha256(wav_a).digest() != hashlib.sha256(wav_b).digest()
    # Upgraded groove tempo: 88-104 BPM (chill-pop energy for Shorts; the old
    # 68-84 ambient bed tested as "not attractive" by the operator).
    assert 88 <= metadata["tempo_bpm"] <= 104

    with wave.open(io.BytesIO(wav_b), "rb") as rendered:
        assert rendered.getnchannels() == 2
        assert rendered.getframerate() == 44_100
        assert rendered.getsampwidth() == 2
        assert rendered.getnframes() == 44_100


def test_original_bgm_metadata_declares_groove_arrangement():
    """The upgraded composer must be a full arrangement, not a 3-layer pad bed."""
    _, metadata = build_original_bgm_wav(duration_sec=2.0, seed_key="short-groove")
    layers = metadata["layers"]
    for required in ("drums", "bass", "chords", "melody"):
        assert required in layers, f"missing layer: {required}"
    assert metadata["style"] == "warm_pop_groove"
    assert 0.0 <= metadata["swing"] <= 0.25


def _rms_per_100ms(wav_bytes: bytes) -> list[float]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as rendered:
        frames = rendered.readframes(rendered.getnframes())
        rate = rendered.getframerate()
    window = rate // 10  # 100 ms of stereo frames
    out = []
    total_frames = len(frames) // 4
    for start in range(0, total_frames - window, window):
        acc = 0.0
        for i in range(start, start + window):
            left, right = struct.unpack_from("<hh", frames, i * 4)
            acc += (left / 32768.0) ** 2 + (right / 32768.0) ** 2
        out.append(math.sqrt(acc / (window * 2)))
    return out


def test_original_bgm_has_rhythmic_dynamics_not_a_flat_pad():
    """A groove has transients: loudness must move between 100ms windows.

    The old pad bed was nearly flat (rms variation ~0); the upgraded
    drums/bass arrangement must show clear beat-level dynamics.
    """
    wav_bytes, _ = build_original_bgm_wav(duration_sec=8.0, seed_key="short-dyn")
    rms = _rms_per_100ms(wav_bytes)
    # Skip the intro/outro fades; look at the groove body.
    body = rms[15:-10]
    assert body, "expected a groove body"
    spread = max(body) - min(body)
    assert spread > 0.02, f"loudness spread {spread:.4f} too flat — no audible rhythm"
