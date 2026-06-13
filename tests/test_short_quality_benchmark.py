from __future__ import annotations

import json
import sys
from pathlib import Path


def test_short_quality_benchmark_dry_run_writes_report(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.run_short_quality_benchmark import run_benchmark

    topics_path = tmp_path / "topics.json"
    reports_dir = tmp_path / "reports"
    topics_path.write_text(
        json.dumps(
            [
                {
                    "topic": "pan integral falso",
                    "format": "myth_or_contradiction",
                    "expected_visual": "bread package / ingredient label / supermarket",
                    "topic_family": "nutrition",
                }
            ]
        ),
        encoding="utf-8",
    )

    report_path = run_benchmark(dry_run=True, topics_path=topics_path, reports_dir=reports_dir)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["version"] == "shorts_quality_v2_6"
    assert report["topics"][0]["hook"]["candidate_count"] >= 8
    assert report["topics"][0]["first_frame"]["has_crop_plan"] is True
    assert report["summary"]["pass_count"] == 1
