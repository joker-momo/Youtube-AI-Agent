"""Competitor video teardown analyzer.

Runs in the MAIN project venv (.venv) which already has cv2/numpy/soundfile —
NOT the tool venv, and NOT importing video_agent. Invoked as a subprocess:

    .venv/bin/python analyzer.py <job_dir>

<job_dir> must contain video.mp4 + audio.wav (from downloader.py); optionally a
sibling transcript <id>.txt for word count. Writes <job_dir>/analysis.json and
<job_dir>/frames/NN.jpg (~20 keyframes for vision review in chat).

All numbers are objective metrics. "music_under_speech" and shot-type are
heuristics — flagged as such in the output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

KEYFRAME_COUNT = 20
CUT_CORR_THRESHOLD = 0.6          # HSV-hist correlation below this = scene cut
SAMPLE_FPS = 3.0                  # frames/sec sampled for cut+motion+color
PROC_WIDTH = 320                  # downscale width for fast CV
F0_MIN_HZ, F0_MAX_HZ = 70.0, 400.0


# --------------------------------------------------------------------------- #
# ffprobe / ffmpeg helpers
# --------------------------------------------------------------------------- #

def probe_format(video: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True, timeout=60,
    )
    data = json.loads(out.stdout or "{}")
    v: dict = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a: dict = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = _parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    dur = float(data.get("format", {}).get("duration") or 0.0)
    return {
        "duration_sec": round(dur, 2),
        "width": w, "height": h,
        "aspect": round(w / h, 3) if h else None,
        "orientation": "landscape" if w >= h else "portrait",
        "fps": round(fps, 2),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
    }


def _parse_fps(ratio: str) -> float:
    try:
        num, den = ratio.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def analyze_loudness(audio: Path) -> dict:
    """Integrated LUFS / true peak / loudness range via ffmpeg loudnorm JSON."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(audio),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out.stderr or "", re.S)
    if not m:
        return {}
    j = json.loads(m.group(0))
    return {
        "integrated_lufs": _f(j.get("input_i")),
        "true_peak_db": _f(j.get("input_tp")),
        "loudness_range_lu": _f(j.get("input_lra")),
        "threshold_lufs": _f(j.get("input_thresh")),
    }


def analyze_silence(audio: Path, duration: float) -> dict:
    """Silence segments via ffmpeg silencedetect -> pause stats + speaking ratio."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(audio),
         "-af", "silencedetect=noise=-30dB:d=0.4", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    durs = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", out.stderr or "")]
    total_sil = sum(durs)
    speaking = max(duration - total_sil, 0.0)
    return {
        "pause_count": len(durs),
        "pause_total_sec": round(total_sil, 1),
        "pause_mean_sec": round(total_sil / len(durs), 2) if durs else 0.0,
        "pause_max_sec": round(max(durs), 2) if durs else 0.0,
        "silence_ratio": round(total_sil / duration, 3) if duration else 0.0,
        "speaking_sec": round(speaking, 1),
    }


# --------------------------------------------------------------------------- #
# Visual: cuts, pacing, motion, color, keyframes
# --------------------------------------------------------------------------- #

def analyze_visual(video: Path, duration: float, frames_dir: Path) -> dict:
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(int(round(fps / SAMPLE_FPS)), 1)

    prev_hist = None
    cut_times: list[float] = []
    motions: list[float] = []
    sats: list[float] = []
    brights: list[float] = []
    contrasts: list[float] = []
    prev_gray = None

    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                small = _resize(frame)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if prev_hist is not None:
                    corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                    if corr < CUT_CORR_THRESHOLD:
                        cut_times.append(idx / fps)
                if prev_gray is not None:
                    motions.append(float(np.mean(cv2.absdiff(gray, prev_gray))))
                sats.append(float(np.mean(hsv[:, :, 1])))
                brights.append(float(np.mean(hsv[:, :, 2])))
                contrasts.append(float(np.std(gray)))
                prev_hist, prev_gray = hist, gray
        idx += 1
    cap.release()

    shots = len(cut_times) + 1
    shot_lengths = _shot_lengths(cut_times, duration)
    keyframes = _extract_keyframes(video, duration, frames_dir)

    return {
        "shot_count": shots,
        "cut_count": len(cut_times),
        "cuts_per_min": round(len(cut_times) / (duration / 60), 2) if duration else 0.0,
        "avg_shot_sec": round(float(np.mean(shot_lengths)), 2) if shot_lengths else None,
        "median_shot_sec": round(float(np.median(shot_lengths)), 2) if shot_lengths else None,
        "motion_index": round(float(np.mean(motions)), 2) if motions else 0.0,
        "motion_label": _motion_label(float(np.mean(motions)) if motions else 0.0),
        "avg_saturation": round(float(np.mean(sats)), 1) if sats else None,
        "avg_brightness": round(float(np.mean(brights)), 1) if brights else None,
        "avg_contrast": round(float(np.mean(contrasts)), 1) if contrasts else None,
        "keyframes": keyframes,
        "keyframe_dir": str(frames_dir),
    }


def _resize(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= PROC_WIDTH:
        return frame
    return cv2.resize(frame, (PROC_WIDTH, int(h * PROC_WIDTH / w)))


def _shot_lengths(cut_times: list[float], duration: float) -> list[float]:
    if not cut_times:
        return [duration] if duration else []
    bounds = [0.0, *cut_times, duration]
    return [b - a for a, b in zip(bounds, bounds[1:], strict=False) if b > a]


def _motion_label(m: float) -> str:
    if m < 4:
        return "very_static"
    if m < 10:
        return "calm"
    if m < 20:
        return "moderate"
    return "dynamic"


def _extract_keyframes(video: Path, duration: float, frames_dir: Path) -> list[dict]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    n = KEYFRAME_COUNT
    out = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        if w > 480:
            frame = cv2.resize(frame, (480, int(h * 480 / w)))
        name = f"{i:02d}.jpg"
        cv2.imwrite(str(frames_dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        out.append({"index": i, "t_sec": round(t, 1), "file": f"frames/{name}"})
    cap.release()
    return out


# --------------------------------------------------------------------------- #
# Voice: WPM (transcript words / speaking time), F0 register, music heuristic
# --------------------------------------------------------------------------- #

def analyze_voice(audio: Path, words: int, speaking_sec: float) -> dict:
    wpm = round(words / (speaking_sec / 60), 1) if speaking_sec and words else None
    f0 = _estimate_f0(audio)
    return {"word_count": words, "wpm": wpm, **f0}


def _estimate_f0(audio: Path) -> dict:
    import soundfile as sf

    y, sr = sf.read(str(audio))
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float64)
    win = int(0.04 * sr)              # 40 ms
    hop = int(0.02 * sr)             # 20 ms
    lo = int(sr / F0_MAX_HZ)
    hi = int(sr / F0_MIN_HZ)
    energy_thresh = np.sqrt(np.mean(y ** 2)) * 0.5 if y.size else 0.0
    pitches: list[float] = []
    for start in range(0, max(len(y) - win, 0), hop):
        frame = y[start:start + win]
        if np.sqrt(np.mean(frame ** 2)) < energy_thresh:
            continue
        frame = frame - frame.mean()
        corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
        if hi >= len(corr):
            continue
        seg = corr[lo:hi]
        if seg.size == 0 or corr[0] <= 0:
            continue
        peak = int(np.argmax(seg)) + lo
        if corr[peak] / corr[0] < 0.3:   # weak periodicity -> unvoiced
            continue
        pitches.append(sr / peak)
    if not pitches:
        return {"f0_median_hz": None, "f0_mean_hz": None, "f0_range_hz": None,
                "voice_register": None}
    arr = np.array(pitches)
    med = float(np.median(arr))
    return {
        "f0_median_hz": round(med, 1),
        "f0_mean_hz": round(float(np.mean(arr)), 1),
        "f0_range_hz": round(float(np.percentile(arr, 90) - np.percentile(arr, 10)), 1),
        "voice_register": _register(med),
    }


def _register(hz: float) -> str:
    if hz < 165:
        return "male / low"
    if hz < 220:
        return "mid (mature female / high male)"
    return "high female"


def music_heuristic(silence_ratio: float, motion_label: str) -> dict:
    """Coarse hint only: low silence ratio often implies continuous background
    music or wall-to-wall narration. NOT a reliable music detector."""
    likely = silence_ratio < 0.08
    return {
        "music_under_speech_likely": likely,
        "basis": f"silence_ratio={silence_ratio} (<0.08 suggests continuous bed)",
        "is_heuristic": True,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _word_count(job_dir: Path) -> int:
    vid = job_dir.name
    for cand in (job_dir.parent / f"{vid}.txt", job_dir / f"{vid}.txt"):
        if cand.exists():
            return len(cand.read_text(encoding="utf-8").split())
    return 0


def _whisper_word_count(audio: Path) -> int:
    """Fallback when no transcript exists: transcribe with Whisper (base, es).

    Slower (runs only when a video has no captions). Returns 0 on any failure.
    """
    try:
        import whisper  # lazy: only when needed

        model = whisper.load_model("base")
        result = model.transcribe(str(audio), language="es", fp16=False)
        return len(str(result.get("text", "")).split())
    except Exception:  # noqa: BLE001 - fallback must never break the analysis
        return 0


def analyze(job_dir: Path) -> dict:
    video = job_dir / "video.mp4"
    audio = job_dir / "audio.wav"
    if not video.exists():
        video = next((p for p in job_dir.glob("video.*") if p.suffix != ".json"), video)
    if not video.exists() or not audio.exists():
        raise FileNotFoundError(f"missing video/audio in {job_dir}")

    fmt = probe_format(video)
    dur = fmt["duration_sec"]
    loud = analyze_loudness(audio)
    sil = analyze_silence(audio, dur)
    visual = analyze_visual(video, dur, job_dir / "frames")
    words = _word_count(job_dir) or _whisper_word_count(audio)
    voice = analyze_voice(audio, words, sil["speaking_sec"])
    music = music_heuristic(sil["silence_ratio"], visual["motion_label"])
    meta = {}
    if (job_dir / "meta.json").exists():
        meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))

    result = {
        "video_id": job_dir.name,
        "format": fmt,
        "composition": {k: visual[k] for k in (
            "shot_count", "cut_count", "cuts_per_min", "avg_shot_sec",
            "median_shot_sec", "motion_index", "motion_label")},
        "color": {k: visual[k] for k in (
            "avg_saturation", "avg_brightness", "avg_contrast")},
        "audio": {**loud, **sil, **music},
        "voice": voice,
        "keyframes": visual["keyframes"],
        "thumbnail": "thumbnail.jpg" if (job_dir / "thumbnail.jpg").exists() else None,
        "metadata": meta,
    }
    (job_dir / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _f(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: analyzer.py <job_dir>", file=sys.stderr)
        sys.exit(2)
    out = analyze(Path(sys.argv[1]))
    print(json.dumps({"ok": True, "video_id": out["video_id"],
                      "frames": len(out["keyframes"])}))
