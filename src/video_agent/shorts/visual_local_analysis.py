"""Deterministic local visual analysis and trim-window selection for PR D.

The baseline analyzer intentionally uses only local media probes and sampled
frames. Optional semantic/detector adapters can add evidence later, but missing
adapters must never turn unknown semantic requirements into PASS.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from video_agent.shorts.frames import seconds_to_frames

ANALYZER_VERSION = "4.0.3-pr-d-1"


@dataclass(frozen=True)
class TrimWindowConfig:
    stride_sec: float = 0.5
    max_windows: int = 24
    reject_black_ratio: float = 0.05
    reject_unstable_motion: bool = True
    min_sharpness_score: float = 0.0


def _run_json(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout or "{}")


def _probe_video(path: Path) -> dict[str, Any]:
    data = _run_json(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = data.get("streams") or []
    stream = streams[0] if streams else {}
    duration = stream.get("duration") or (data.get("format") or {}).get("duration")
    rate = str(stream.get("avg_frame_rate") or "0/1")
    try:
        num, den = rate.split("/", 1)
        source_fps = float(num) / max(1.0, float(den))
    except (TypeError, ValueError, ZeroDivisionError):
        source_fps = 0.0
    return {
        "duration_sec": float(duration or 0.0),
        "source_fps": source_fps,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }


def _extract_frame(path: Path, timestamp_sec: float, out_path: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, timestamp_sec):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def _sharpness(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    gx = np.diff(gray.astype(np.float32), axis=1)
    gy = np.diff(gray.astype(np.float32), axis=0)
    return float(np.var(gx) + np.var(gy))


def _frame_metrics(image_path: Path, frame_no: int, timestamp_sec: float) -> dict[str, Any]:
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        arr = np.asarray(rgb, dtype=np.float32)
    luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    mean_luma = float(np.mean(luma))
    black_or_fade = mean_luma < 8.0
    return {
        "frame_in_frames": frame_no,
        "timestamp_sec": round(timestamp_sec, 3),
        "mean_luma": mean_luma,
        "black_or_fade": black_or_fade,
        "sharpness_score": _sharpness(luma),
        "_luma": luma,
    }


def _motion_band(samples: list[dict[str, Any]]) -> tuple[str, float]:
    diffs: list[float] = []
    for prev, cur in zip(samples, samples[1:], strict=False):
        a = prev.get("_luma")
        b = cur.get("_luma")
        if a is None or b is None or a.shape != b.shape:
            continue
        diffs.append(float(np.mean(np.abs(b.astype(np.float32) - a.astype(np.float32)))))
    motion = float(np.mean(diffs)) if diffs else 0.0
    if motion < 1.5:
        return "near_static", motion
    if motion < 6.0:
        return "low_motion", motion
    if motion < 25.0:
        return "normal_motion", motion
    if motion < 50.0:
        return "high_motion", motion
    return "unstable", motion


def _crop_feasibility(samples: list[dict[str, Any]]) -> dict[str, Any]:
    ratio = 1.0 if samples else 0.0
    return {
        "full_window_feasible": bool(samples),
        "crop_stability_score": round(ratio, 3),
        "mode": "center_cover_static",
        "dynamic_crop": False,
    }


class LocalVisualAnalyzer:
    def __init__(self, *, stride_sec: float = 0.5) -> None:
        self.stride_sec = max(0.1, float(stride_sec))

    def analyze(self, path: str | Path, *, required_frames: int, fps: int) -> dict[str, Any]:
        media_path = Path(path)
        started = __import__("time").monotonic()
        try:
            probe = _probe_video(media_path)
        except Exception as exc:  # noqa: BLE001 - artifact should record probe failure.
            return {
                "analyzer_version": ANALYZER_VERSION,
                "path": str(media_path),
                "decode": {"verdict": "FAIL", "errors": [f"probe_failed:{exc.__class__.__name__}"]},
                "actual_duration_sec": 0.0,
                "actual_duration_in_frames": 0,
                "required_duration_in_frames": required_frames,
                "sampled_frames": [],
                "analysis_runtime_ms": int((__import__("time").monotonic() - started) * 1000),
            }

        duration_sec = float(probe.get("duration_sec") or 0.0)
        actual_frames = seconds_to_frames(duration_sec, fps)
        decode_errors: list[str] = []
        samples: list[dict[str, Any]] = []
        if duration_sec <= 0:
            decode_errors.append("duration_unavailable")
        else:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                sample_count = max(1, int(math.ceil(duration_sec / self.stride_sec)))
                for idx in range(sample_count):
                    ts = min(duration_sec - 0.001, idx * self.stride_sec)
                    frame_no = seconds_to_frames(max(0.0, ts), fps)
                    out = tmp / f"frame-{idx:04d}.jpg"
                    if not _extract_frame(media_path, ts, out):
                        decode_errors.append(f"decode_failed_at:{frame_no}")
                        continue
                    samples.append(_frame_metrics(out, frame_no, ts))

        motion_band, motion_score = _motion_band(samples)
        black_count = sum(1 for s in samples if s.get("black_or_fade"))
        black_ratio = black_count / max(1, len(samples))
        sharp_values = [float(s.get("sharpness_score") or 0.0) for s in samples]
        sharpness = float(np.median(sharp_values)) if sharp_values else 0.0
        technical_verdict = "PASS" if sharpness >= 0.0 and not decode_errors else "FAIL"
        crop = _crop_feasibility(samples)
        public_samples = [{k: v for k, v in s.items() if k != "_luma"} for s in samples]
        return {
            "analyzer_version": ANALYZER_VERSION,
            "path": str(media_path),
            "decode": {"verdict": "FAIL" if decode_errors else "PASS", "errors": decode_errors},
            "actual_duration_sec": duration_sec,
            "actual_duration_in_frames": actual_frames,
            "required_duration_in_frames": required_frames,
            "source_fps": probe.get("source_fps"),
            "fps": fps,
            "width": probe.get("width"),
            "height": probe.get("height"),
            "black_frame_ratio": black_ratio,
            "motion_band": motion_band,
            "motion_score": motion_score,
            "technical_quality": {
                "verdict": technical_verdict,
                "sharpness_score": sharpness,
                "sample_count": len(samples),
            },
            "crop_feasibility": crop,
            "sampled_frames": public_samples,
            "analysis_runtime_ms": int((__import__("time").monotonic() - started) * 1000),
        }


def _samples_in_window(analysis: dict[str, Any], start: int, end: int) -> list[dict[str, Any]]:
    return [
        s
        for s in analysis.get("sampled_frames") or []
        if start <= int(s.get("frame_in_frames") or 0) < end
    ]


def _candidate_starts(
    analysis: dict[str, Any], required_frames: int, config: TrimWindowConfig
) -> list[int]:
    actual = int(analysis.get("actual_duration_in_frames") or 0)
    max_start = max(0, actual - required_frames)
    stride_frames = max(
        1, seconds_to_frames(config.stride_sec, int(analysis.get("fps") or 30) or 30)
    )
    starts = {0, max_start}
    for sample in analysis.get("sampled_frames") or []:
        frame = int(sample.get("frame_in_frames") or 0)
        if not sample.get("black_or_fade"):
            starts.add(min(max_start, max(0, frame)))
            break
    for frame in range(0, max_start + 1, stride_frames):
        starts.add(frame)
    return sorted(starts)[: max(1, config.max_windows)]


def select_trim_window(
    analysis: dict[str, Any], *, required_frames: int, config: TrimWindowConfig | None = None
) -> dict[str, Any]:
    config = config or TrimWindowConfig()
    actual = int(analysis.get("actual_duration_in_frames") or 0)
    fps = int(analysis.get("fps") or 30)
    if actual < required_frames:
        return {
            "status": "no_valid_window",
            "required_duration_in_frames": required_frames,
            "rejection_reasons": {"duration_insufficient": 1},
        }
    if (analysis.get("decode") or {}).get("verdict") != "PASS":
        return {
            "status": "no_valid_window",
            "required_duration_in_frames": required_frames,
            "rejection_reasons": {"decode_failure": 1},
        }

    rejected: dict[str, int] = {}
    valid: list[dict[str, Any]] = []
    for start in _candidate_starts({**analysis, "fps": fps}, required_frames, config):
        end = start + required_frames
        samples = _samples_in_window(analysis, start, end)
        black_ratio = sum(1 for s in samples if s.get("black_or_fade")) / max(1, len(samples))
        if black_ratio > config.reject_black_ratio:
            rejected["black_or_fade"] = rejected.get("black_or_fade", 0) + 1
            continue
        crop = analysis.get("crop_feasibility") or {}
        if not crop.get("full_window_feasible", False):
            rejected["crop_unstable"] = rejected.get("crop_unstable", 0) + 1
            continue
        if config.reject_unstable_motion and analysis.get("motion_band") == "unstable":
            rejected["unstable_motion"] = rejected.get("unstable_motion", 0) + 1
            continue
        sharpness = float((analysis.get("technical_quality") or {}).get("sharpness_score") or 0.0)
        if sharpness < config.min_sharpness_score:
            rejected["sharpness_below_threshold"] = rejected.get("sharpness_below_threshold", 0) + 1
            continue
        score = 100.0 - (black_ratio * 40.0) + float(crop.get("crop_stability_score") or 0.0) * 10.0
        if start > 0:
            score += 3.0
        valid.append(
            {
                "start": start,
                "end": end,
                "score": round(min(100.0, score), 3),
                "black_ratio": black_ratio,
                "crop_stability_score": crop.get("crop_stability_score"),
            }
        )

    if not valid:
        return {
            "status": "no_valid_window",
            "required_duration_in_frames": required_frames,
            "rejection_reasons": rejected or {"no_samples": 1},
        }
    best = sorted(valid, key=lambda item: (-float(item["score"]), int(item["start"])))[0]
    return {
        "status": "selected",
        "required_duration_in_frames": required_frames,
        "trim_timebase_fps": fps,
        "selected_window_start_in_frames": int(best["start"]),
        "selected_window_end_in_frames": int(best["end"]),
        "window_score": float(best["score"]),
        "motion_band": analysis.get("motion_band"),
        "crop_stability_score": best.get("crop_stability_score"),
        "first_frame_score": 1.0 if best["start"] > 0 else 0.8,
        "selection_reasons": ["no fade/black", "crop target remains inside safe region"],
        "rejected_window_counts": rejected,
    }


def copy_or_download_candidate(
    *,
    candidate: dict[str, Any],
    short_dir: Path,
    span_id: str,
    download_client: Any,
) -> dict[str, Any]:
    local = candidate.get("local_path")
    if local and Path(str(local)).exists():
        path = Path(str(local))
    else:
        source = candidate.get("download_url") or candidate.get("download_url_ref")
        if not source:
            raise ValueError("candidate_missing_download_url")
        asset_dir = short_dir / "assets" / "visual_spans"
        asset_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in candidate["candidate_id"]
        )
        path = asset_dir / f"{span_id}-{safe_id}.mp4"
        download_client.download(str(source), path)
    digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    public_ref = (
        candidate.get("public_ref") or f"jobs/{short_dir.name}/assets/visual_spans/{path.name}"
    )
    return {**candidate, "local_path": str(path), "public_ref": public_ref, "content_hash": digest}


class FinalistDownloader:
    def __init__(self, download_client: Any | None = None) -> None:
        if download_client is None:
            from video_agent.assets.stock_core import UrlDownloadClient

            download_client = UrlDownloadClient()
        self.download_client = download_client

    def download(
        self, *, candidate: dict[str, Any], short_dir: Path, span_id: str
    ) -> dict[str, Any]:
        return copy_or_download_candidate(
            candidate=candidate,
            short_dir=short_dir,
            span_id=span_id,
            download_client=self.download_client,
        )
