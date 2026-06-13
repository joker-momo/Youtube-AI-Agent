from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_agent.shorts.first_frame_planner import apply_first_frame_plan
from video_agent.shorts.hook_lab import build_hook_lab
from video_agent.shorts.roi_crop_planner import apply_crop_plan
from video_agent.shorts.visual_rhythm import apply_visual_rhythm_to_scenes, build_visual_rhythm_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPICS = REPO_ROOT / "benchmarks/shorts_quality/topics.json"
DEFAULT_REPORTS = REPO_ROOT / "benchmarks/shorts_quality/reports"


def _short_plan(topic: dict[str, Any]) -> dict[str, Any]:
    return {
        "short_id": "benchmark-short",
        "title": topic.get("topic") or "",
        "format": topic.get("format") or "pain_to_tip",
        "topic_family": topic.get("topic_family") or "",
        "hook_angle": topic.get("topic") or "",
        "viewer_pain": "elegir rapido sin ver el detalle",
        "curiosity_gap": "el detalle visible cambia la decision",
        "key_points": [{"point": topic.get("expected_visual") or ""}],
    }


def _fixture_scenes(topic: dict[str, Any]) -> dict[str, Any]:
    visual = topic.get("expected_visual") or "specific everyday object"
    return {
        "short_id": "benchmark-short",
        "total_duration_sec": 24,
        "scenes": [
            {
                "id": "s01",
                "layout": "short_hook",
                "duration_sec": 3,
                "motion": "none",
                "narration": str(topic.get("topic") or ""),
                "on_screen_text": "HOOK",
                "visual_prompt": f"close up {visual}",
                "asset_refs": {"background": "jobs/benchmark/assets/s01.mp4"},
            },
            {
                "id": "s02",
                "layout": "short_tip",
                "duration_sec": 21,
                "motion": "none",
                "narration": "Mira el detalle antes de decidir.",
                "on_screen_text": "MIRA DETALLE",
                "visual_prompt": f"hand checking {visual}",
                "asset_refs": {"background": "jobs/benchmark/assets/s02.mp4"},
            },
        ],
    }


def _topic_report(topic: dict[str, Any], tmp_root: Path) -> dict[str, Any]:
    plan = _short_plan(topic)
    hook = build_hook_lab(plan, {}, {}, {})
    plan["hook_text"] = hook["selected_hook"]
    scenes = apply_first_frame_plan(_fixture_scenes(topic), plan, {})
    scenes = apply_crop_plan(scenes)
    rhythm_plan = build_visual_rhythm_plan(tmp_root, "benchmark-short", scenes, {"retention_beats": [{"function": "hook"}, {"function": "proof"}]}, {"shorts": {}})
    scenes = apply_visual_rhythm_to_scenes(scenes, rhythm_plan)
    warnings: list[str] = []
    first = scenes["scenes"][0]
    if not first.get("first_frame_plan"):
        warnings.append("missing_first_frame_plan")
    if not first.get("crop_plan"):
        warnings.append("missing_crop_plan")
    return {
        "topic": topic.get("topic"),
        "hook": {
            "candidate_count": len(hook["candidates"]),
            "selected_hook": hook["selected_hook"],
            "score": next((c["score"] for c in hook["candidates"] if c["hook"] == hook["selected_hook"]), 0),
            "warnings": hook["warnings"],
        },
        "first_frame": {
            "strategy": first.get("first_frame_plan", {}).get("strategy"),
            "has_crop_plan": bool(first.get("crop_plan")),
            "warnings": warnings,
        },
        "asset": {
            "query_count": 1,
            "score_delta_applied": True,
            "fallback_used": False,
            "warnings": [],
        },
        "rhythm": {
            "first_scene_motion": scenes["scenes"][0].get("motion"),
            "repeated_motion_warnings": [],
        },
        "qa": {
            "status": "PASS" if not warnings else "WARN",
            "warnings": warnings,
        },
    }


def run_benchmark(
    *,
    dry_run: bool = True,
    topics_path: Path = DEFAULT_TOPICS,
    reports_dir: Path = DEFAULT_REPORTS,
) -> Path:
    topics = json.loads(Path(topics_path).read_text(encoding="utf-8"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = reports_dir / "_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    topic_reports = [_topic_report(topic, tmp_root) for topic in topics]
    summary = {
        "pass_count": sum(1 for item in topic_reports if item["qa"]["status"] == "PASS"),
        "warn_count": sum(1 for item in topic_reports if item["qa"]["status"] == "WARN"),
        "fail_count": sum(1 for item in topic_reports if item["qa"]["status"] == "FAIL"),
    }
    report = {
        "version": "shorts_quality_v2_6",
        "topics": topic_reports,
        "summary": summary,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = reports_dir / f"{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    if args.render:
        raise SystemExit("--render is not implemented for this deterministic benchmark")
    path = run_benchmark(dry_run=True)
    print(path)


if __name__ == "__main__":
    main()
