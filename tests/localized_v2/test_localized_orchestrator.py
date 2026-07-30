from __future__ import annotations

from pathlib import Path

import pytest

from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.orchestrator import (
    LocalizedPromptRunner,
    LocalizedQARejected,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.prompts import PromptEnvelope
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.worker import LocalizedWorker

from .locale_fixtures import snapshots

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


class FakeLocalizedProvider:
    name = "fake-localized"

    def __init__(self, *, qa_verdict: str = "PASS"):
        self.qa_verdict = qa_verdict
        self.calls: list[str] = []

    def generate(self, prompt: PromptEnvelope) -> dict:
        self.calls.append(prompt.stage)
        locale = prompt.payload.get("channel", {}).get("locale", "en-US")
        if prompt.stage == "idea":
            return {
                "schemaVersion": "localized-idea-v2/v1",
                "locale": locale,
                "angle": "A practical evidence-based routine",
                "audiencePromise": "Understand one realistic daily habit",
                "localRelevance": "Ordinary daily life",
                "evidenceQuestions": [
                    "What does current evidence suggest?",
                    "Who should seek individual advice?",
                ],
            }
        if prompt.stage == "script":
            return {
                "schemaVersion": "localized-script-v2/v1",
                "locale": locale,
                "title": "A realistic daily walking habit",
                "sections": [
                    {
                        "id": "opening",
                        "narration": (
                            "Research suggests that regular walking may support "
                            "wellbeing for many adults."
                        ),
                    }
                ],
            }
        if prompt.stage == "scenes":
            return {
                "schemaVersion": "localized-scenes-v2/v1",
                "locale": locale,
                "scenes": [
                    {
                        "id": "opening",
                        "narration": (
                            "Research suggests that regular walking may support "
                            "wellbeing for many adults."
                        ),
                        "visualType": "video",
                        "visualPrompt": "An ordinary adult taking a calm daily walk",
                        "searchBrief": {
                            "language": "en",
                            "queries": ["adult daily walking routine park"],
                        },
                    }
                ],
            }
        if prompt.stage == "seo":
            return {
                "schemaVersion": "localized-seo-v2/v1",
                "locale": locale,
                "title": "A Realistic Daily Walking Habit After 45",
                "description": (
                    "Research suggests that regular walking may support wellbeing."
                ),
                "tags": ["healthy aging", "walking", "daily habits"],
                "thumbnailText": "WALK WITH PURPOSE",
                "pinnedComment": "What helps you keep a realistic walking routine?",
            }
        return {
            "schemaVersion": "localized-qa-v2/v1",
            "locale": locale,
            "verdict": self.qa_verdict,
            "failures": (
                []
                if self.qa_verdict == "PASS"
                else [
                    {
                        "code": "EVIDENCE_GAP",
                        "field": "script.sections.0",
                        "message": "A health statement needs better qualification.",
                    }
                ]
            ),
        }


def _worker(
    tmp_path: Path,
    provider: FakeLocalizedProvider,
) -> tuple[LocalizedWorker, LocalizedQueue]:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "localized-runtime", legacy_jobs_root=legacy)
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db)
    channel, locale_pack = snapshots("en-US")
    queue.create_job(
        JobInput(
            job_id="localized-job",
            channel_id=channel["channelId"],
            locale="en-US",
            topic="A realistic walking habit",
            channel_snapshot=channel,
            locale_snapshot=locale_pack,
        )
    )
    runner = LocalizedPromptRunner(paths, SCHEMA_ROOT, provider)
    return LocalizedWorker("localized-worker", paths, queue, runner), queue


def test_prompt_orchestrator_validates_and_promotes_each_stage(tmp_path: Path) -> None:
    provider = FakeLocalizedProvider()
    worker, queue = _worker(tmp_path, provider)

    assert worker.run_once()

    assert provider.calls == ["idea", "script", "scenes", "seo", "qa"]
    assert queue.completed_stages("localized-job") == (
        "idea",
        "script",
        "scenes",
        "seo",
        "qa",
    )
    assert queue.get_job("localized-job")["status"] == "COMPLETED"
    assert [artifact["name"] for artifact in queue.list_artifacts("localized-job")] == [
        "idea.json",
        "script.json",
        "scenes.json",
        "seo.json",
        "qa.json",
    ]


def test_failed_qa_is_structured_and_never_promoted(tmp_path: Path) -> None:
    provider = FakeLocalizedProvider(qa_verdict="FAIL")
    worker, queue = _worker(tmp_path, provider)

    with pytest.raises(LocalizedQARejected):
        worker.run_once()

    job = queue.get_job("localized-job")
    assert job["status"] == "FAILED"
    assert job["failure"]["code"] == "LOCALIZED_QA_REJECTED"
    assert job["failure"]["locale"] == "en-US"
    assert job["failure"]["stage"] == "qa"
    assert job["failure"]["artifact"] == "qa"
    assert "qa.json" not in {
        artifact["name"] for artifact in queue.list_artifacts("localized-job")
    }
