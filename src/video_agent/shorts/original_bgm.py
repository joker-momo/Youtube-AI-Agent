"""Create reproducible, original background music for Shorts.

The module synthesizes every PCM sample from math primitives.  It never reads
an audio clip, sample pack, artist reference, or third-party music file, so a
Short can retain an auditable provenance record beside its rendered audio.

The composer renders a warm pop groove (drums + bass + electric-piano chords +
pentatonic melody with swing and sidechain ducking) rather than an ambient pad
bed: infographic Shorts carry NO narration, so the music alone must hold the
viewer for ~15 seconds.
"""
from __future__ import annotations

import hashlib
import io
import math
import random
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

from video_agent.shorts import paths
from video_agent.storage.atomic import atomic_write_json

SAMPLE_RATE = 44_100
CHANNELS = 2
SAMPLE_WIDTH = 2

# Major-pentatonic intervals keep the melody warm and consonant for the
# channel's wellness audience without borrowing an identifiable musical work.
_PENTATONIC = (0, 2, 4, 7, 9)

# Pop chord loops as semitone roots relative to the tonic, with triad quality.
# (I-vi-IV-V family: familiar, warm, resolves cleanly at the loop point.)
_PROGRESSIONS = (
    ((0, "maj"), (9, "min"), (5, "maj"), (7, "maj")),   # I  vi IV V
    ((0, "maj"), (5, "maj"), (9, "min"), (7, "maj")),   # I  IV vi V
    ((9, "min"), (5, "maj"), (0, "maj"), (7, "maj")),   # vi IV I  V
)
_TRIADS = {"maj": (0, 4, 7), "min": (0, 3, 7)}

# Per-bar step patterns, 8 slots of 8th notes (velocity 0..1).
_KICK_PATTERNS = (
    (1.0, 0, 0, 0, 0.95, 0, 0.4, 0),
    (1.0, 0, 0, 0.35, 0.95, 0, 0, 0.3),
    (1.0, 0, 0.3, 0, 0.9, 0, 0.45, 0),
)
_SNARE_PATTERN = (0, 0, 1.0, 0, 0, 0, 1.0, 0)          # backbeat on 2 & 4
_HAT_PATTERN = (0.5, 0.9, 0.45, 0.85, 0.5, 0.9, 0.45, 0.95)
_BASS_PATTERNS = (
    (1.0, 0, 0, 0.8, 0, 0.9, 0, 0.55),
    (1.0, 0, 0.6, 0, 0.85, 0, 0.7, 0),
    (1.0, 0, 0, 0.75, 0.85, 0, 0, 0.6),
)


def _seed_int(seed_key: str) -> int:
    return int.from_bytes(hashlib.sha256(seed_key.encode("utf-8")).digest()[:8], "big")


def _midi_hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def _soft_tone(frequency: float, seconds: float) -> float:
    """A low-harmonic oscillator that is gentler than a raw sine stack."""
    phase = math.tau * frequency * seconds
    return (
        math.sin(phase)
        + 0.22 * math.sin(phase * 2.0)
        + 0.07 * math.sin(phase * 3.0)
    ) / 1.29


def _noise(frame: int) -> float:
    """Deterministic white noise from the frame index (stateless LCG hash)."""
    n = (frame * 1103515245 + 12345) & 0x7FFFFFFF
    n = (n * 1103515245 + 12345) & 0x7FFFFFFF
    return (n / 0x3FFFFFFF) - 1.0


def _fade(seconds: float, duration_sec: float) -> float:
    fade_in = min(0.25, duration_sec * 0.2)
    fade_out = min(0.9, duration_sec * 0.35)
    if seconds < fade_in:
        return 0.5 - 0.5 * math.cos(math.pi * seconds / fade_in)
    remaining = duration_sec - seconds
    if remaining < fade_out:
        return 0.5 - 0.5 * math.cos(math.pi * max(remaining, 0.0) / fade_out)
    return 1.0


def _swing_slot(beat: float, swing: float) -> tuple[int, float]:
    """8th-note slot index within the bar plus seconds-agnostic slot fraction.

    Off-beat 8ths are delayed by ``swing`` of an 8th so the groove shuffles
    instead of marching on a rigid grid.
    """
    eighth = beat * 2.0
    slot = int(eighth) % 8
    frac = eighth - int(eighth)
    if slot % 2 == 1:  # off-beat 8th starts late under swing
        frac = max(0.0, frac - swing) / max(1e-6, 1.0 - swing)
    return slot, frac


def build_original_bgm_wav(
    *,
    duration_sec: float,
    seed_key: str,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[bytes, dict[str, Any]]:
    """Return deterministic stereo WAV bytes and their provenance metadata.

    ``seed_key`` is normally the immutable Short id.  The same id and duration
    always yield the exact same PCM, which makes render retries reproducible.
    """
    if not 0.5 <= float(duration_sec) <= 60.0:
        raise ValueError("duration_sec must be between 0.5 and 60 seconds")
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"sample_rate must be {SAMPLE_RATE} for the Shorts audio contract")
    if not str(seed_key).strip():
        raise ValueError("seed_key is required for deterministic original BGM")

    duration = float(duration_sec)
    frame_count = round(duration * sample_rate)
    rng = random.Random(_seed_int(seed_key))
    tempo_bpm = rng.randrange(88, 105)
    tonic_midi = rng.choice((50, 52, 53, 55, 57))
    progression = rng.choice(_PROGRESSIONS)
    kick_pattern = rng.choice(_KICK_PATTERNS)
    bass_pattern = rng.choice(_BASS_PATTERNS)
    swing = rng.uniform(0.08, 0.16)
    # Two-bar melody motif: one pentatonic note-or-rest per 8th slot (16 slots),
    # denser on off-beats so the line dances over the backbeat.
    motif: list[float | None] = []
    for slot in range(16):
        density = 0.62 if slot % 2 else 0.42
        if rng.random() < density:
            motif.append(rng.choice(_PENTATONIC) + rng.choice((12, 12, 24)))
        else:
            motif.append(None)
    if motif[0] is None:  # the motif must open on a note (hook starts moving)
        motif[0] = 12.0
    echo_sec = (60.0 / tempo_bpm) * 0.75  # dotted-8th melody echo

    beat_seconds = 60.0 / tempo_bpm
    intro_sec = min(1.25, duration * 0.12)

    raw = bytearray(frame_count * CHANNELS * SAMPLE_WIDTH)

    def melody_tone(seconds: float) -> float:
        """Melody voice as a pure function of time (enables the echo tap)."""
        if seconds < 0:
            return 0.0
        beat = seconds / beat_seconds
        slot16 = int(beat * 2.0) % 16
        note = motif[slot16]
        if note is None:
            return 0.0
        _, frac = _swing_slot(beat % 4.0, swing)
        env = math.exp(-6.5 * frac * (beat_seconds * 0.5)) * min(1.0, frac * 40.0)
        return _soft_tone(_midi_hz(tonic_midi + note), seconds) * env

    for frame in range(frame_count):
        seconds = frame / sample_rate
        beat = seconds / beat_seconds
        bar = int(beat) // 4
        bar_beat = beat % 4.0
        slot, slot_frac = _swing_slot(bar_beat, swing)
        slot_sec = slot_frac * (beat_seconds * 0.5)
        chord_semitone, quality = progression[bar % len(progression)]
        chord_root = tonic_midi + chord_semitone
        groove_on = seconds >= intro_sec

        # --- drums -----------------------------------------------------------
        kick = snare = hat = 0.0
        kick_vel = kick_pattern[slot] if groove_on else 0.0
        if kick_vel:
            # Pitch-swept sine thump: phase integral of f(t) = 150 * exp(-25 t).
            sweep_phase = math.tau * 150.0 / 25.0 * (1.0 - math.exp(-25.0 * slot_sec))
            kick = math.sin(sweep_phase) * math.exp(-9.0 * slot_sec) * kick_vel
        snare_vel = _SNARE_PATTERN[slot] if groove_on else 0.0
        if snare_vel:
            body = 0.3 * math.sin(math.tau * 185.0 * slot_sec)
            snare = (_noise(frame) * 0.8 + body) * math.exp(-16.0 * slot_sec) * snare_vel
        hat_vel = _HAT_PATTERN[slot] if groove_on else 0.0
        if hat_vel:
            # Crude highpass: difference of adjacent noise samples brightens it.
            bright = _noise(frame) - _noise(frame - 1)
            hat = bright * math.exp(-38.0 * slot_sec) * hat_vel

        # Sidechain pump: everything tonal ducks under the kick's envelope.
        duck = 1.0 - 0.45 * (math.exp(-9.0 * slot_sec) * kick_vel)

        # --- bass -------------------------------------------------------------
        bass = 0.0
        bass_vel = bass_pattern[slot] if groove_on else 0.0
        if bass_vel:
            env = math.exp(-5.0 * slot_sec) * min(1.0, slot_frac * 30.0)
            bass = _soft_tone(_midi_hz(chord_root - 24), seconds) * env * bass_vel

        # --- chords (struck EP with tremolo, plus an off-beat stab) -----------
        t_in_bar = bar_beat * beat_seconds
        tremolo = 1.0 + 0.12 * math.sin(math.tau * 5.3 * seconds)
        chord_env = math.exp(-1.05 * t_in_bar) * tremolo
        # Off-beat stab on the "and of 2" keeps the groove pushing forward.
        stab_t = t_in_bar - 1.5 * beat_seconds
        stab_env = math.exp(-7.0 * stab_t) * 0.5 if stab_t >= 0 else 0.0
        chord = 0.0
        for interval in (*_TRIADS[quality], 14):  # triad + 9th for warmth
            note_hz = _midi_hz(chord_root + interval)
            chord += _soft_tone(note_hz, seconds) + 0.35 * _soft_tone(note_hz * 1.003, seconds)
        chord = chord / 5.4 * (chord_env + stab_env)

        # --- melody with a dotted-8th echo tap ---------------------------------
        melody = 0.0
        if groove_on:
            melody = melody_tone(seconds) + 0.35 * melody_tone(seconds - echo_sec)

        # --- intro riser (noise sweep + rising tone into the first downbeat) ---
        riser = 0.0
        if seconds < intro_sec:
            lift = seconds / intro_sec
            riser = (
                _noise(frame) * 0.22 * lift * lift
                + 0.3 * math.sin(math.tau * _midi_hz(tonic_midi - 12) * (1.0 + 0.6 * lift) * seconds)
            ) * lift

        signal = (
            0.30 * kick
            + 0.16 * snare
            + 0.075 * hat
            + 0.20 * bass * duck
            + 0.16 * chord * duck
            + 0.12 * melody * duck
            + 0.18 * riser
        ) * _fade(seconds, duration)
        # Gentle soft-clip glues the mix and tames stacked transients.
        signal = math.tanh(1.6 * signal) / math.tanh(1.6)

        # Slow stereo drift plus wider hats/melody keeps the bed alive but centred.
        pan = 0.05 * math.sin(math.tau * seconds / 5.0)
        side = 0.12 * (hat * 0.5 + melody * 0.4)
        left = max(-1.0, min(1.0, signal * (1.0 - pan) + side))
        right = max(-1.0, min(1.0, signal * (1.0 + pan) - side))
        struct.pack_into("<hh", raw, frame * 4, int(left * 32767), int(right * 32767))

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(raw)
    seed_hash = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    return output.getvalue(), {
        "schema_version": 2,
        "source": "procedural_synthesis",
        "copyright_basis": "original_algorithmic_composition",
        "external_audio_inputs": [],
        "seed_sha256": seed_hash,
        "duration_sec": duration,
        "sample_rate": sample_rate,
        "channels": CHANNELS,
        "tempo_bpm": tempo_bpm,
        "tonic_midi": tonic_midi,
        "progression": [f"{semi}:{quality}" for semi, quality in progression],
        "swing": round(swing, 3),
        "layers": ["drums", "bass", "chords", "melody", "riser"],
        "style": "warm_pop_groove",
        "mood": "warm_wellness",
    }


def create_original_bgm(
    short_dir: Path,
    *,
    duration_sec: float,
    seed_key: str | None = None,
    bitrate: str = "192k",
) -> Path:
    """Synthesize and encode the reusable Short BGM plus an audit artifact."""
    short_dir = Path(short_dir)
    key = seed_key or short_dir.name
    wav_bytes, metadata = build_original_bgm_wav(duration_sec=duration_sec, seed_key=key)
    audio_dir = short_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / "infographic_bgm.source.wav"
    out_path = audio_dir / "infographic_bgm.m4a"
    wav_path.write_bytes(wav_bytes)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path), "-c:a", "aac", "-b:a", bitrate,
                "-ar", str(SAMPLE_RATE), str(out_path),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        wav_path.unlink(missing_ok=True)

    metadata.update({
        "output": f"audio/{out_path.name}",
        "encoding": {"codec": "aac", "bitrate": bitrate},
    })
    json_dir = short_dir / paths.SHORT_JSON_SUBDIR
    json_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(json_dir / "original_bgm.json", metadata)
    return out_path
