from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from video_agent.localized_v2.assets import LocalizedAssetPipeline
from video_agent.localized_v2.audio.capabilities import VoiceCapabilityRegistry
from video_agent.localized_v2.audio.tts import LocalizedTTS
from video_agent.localized_v2.brand_assets import BrandClip
from video_agent.localized_v2.channel_registry import ROLLOUT_ORDER, ChannelRegistry
from video_agent.localized_v2.config import ContractValidationError, load_channel_config
from video_agent.localized_v2.dashboard.service import DashboardService, EnabledChannel
from video_agent.localized_v2.job_state import JobInput
from video_agent.localized_v2.orchestrator import (
    LocalizedMediaOrchestrator,
    LocalizedOrchestrator,
    LocalizedPromptRunner,
)
from video_agent.localized_v2.paths import RuntimePaths
from video_agent.localized_v2.preflight import CapabilityInventory
from video_agent.localized_v2.prompts import PromptEnvelope
from video_agent.localized_v2.queue import LocalizedQueue
from video_agent.localized_v2.registry import SUPPORTED_LOCALES, LocaleRegistry
from video_agent.localized_v2.runtime import LocalizedRuntime
from video_agent.localized_v2.worker import LocalizedWorker

from .audio_fixtures import FakeTTSBackend
from .locale_fixtures import snapshots
from .test_media_orchestrator import MP4, FakeAssetProvider, FakeRenderer

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas"
LOCALE_ROOT = REPO_ROOT / "configs" / "localized-v2" / "locales"
CHANNEL_ROOT = REPO_ROOT / "configs" / "localized-v2" / "channels"

NATIVE = {
    "en-US": {
        "angle": "A practical evidence-based routine",
        "promise": "Understand one realistic daily habit",
        "relevance": "Ordinary life in the United States",
        "question": "What does current evidence suggest?",
        "advice": "Who should seek individual advice?",
        "title": "A realistic daily walking habit",
        "narration": "Research suggests that regular walking may support wellbeing for many adults.",
        "visual": "An ordinary older adult taking a calm daily walk",
        "seoTitle": "A Realistic Daily Walking Habit After 45",
        "description": "Research suggests that regular walking may support wellbeing.",
        "tags": ["healthy aging", "walking", "daily habits"],
        "thumbnail": "WALK WITH PURPOSE",
        "comment": "What helps you keep a realistic walking routine?",
    },
    "fr-FR": {
        "angle": "Une routine pratique fondée sur des données",
        "promise": "Comprendre une habitude quotidienne réaliste",
        "relevance": "La vie quotidienne en France",
        "question": "Que suggèrent les données actuelles ?",
        "advice": "Qui devrait demander un conseil personnalisé ?",
        "title": "Une habitude de marche réaliste",
        "narration": "Les recherches suggèrent que marcher régulièrement pourrait contribuer au bien-être de nombreux adultes.",
        "visual": "Un adulte âgé fait une promenade quotidienne calme",
        "seoTitle": "Une habitude de marche réaliste après 45 ans",
        "description": "Les recherches suggèrent que la marche régulière pourrait contribuer au bien-être.",
        "tags": ["bien vieillir", "marche", "habitudes quotidiennes"],
        "thumbnail": "MARCHER CHAQUE JOUR",
        "comment": "Quelle habitude vous aide à marcher régulièrement ?",
    },
    "pt-BR": {
        "angle": "Uma rotina prática baseada em evidências",
        "promise": "Entender um hábito diário realista",
        "relevance": "A vida cotidiana no Brasil",
        "question": "O que os estudos atuais sugerem?",
        "advice": "Quem deve buscar orientação individual?",
        "title": "Um hábito realista de caminhada diária",
        "narration": "Estudos sugerem que caminhar regularmente pode ajudar a apoiar o bem-estar de muitos adultos.",
        "visual": "Uma pessoa mais velha fazendo uma caminhada diária tranquila",
        "seoTitle": "Um hábito realista de caminhada depois dos 45",
        "description": "Estudos sugerem que a caminhada regular pode ajudar a apoiar o bem-estar.",
        "tags": ["envelhecimento saudável", "caminhada", "hábitos diários"],
        "thumbnail": "CAMINHE TODO DIA",
        "comment": "O que ajuda você a manter uma rotina de caminhada?",
    },
    "ko-KR": {
        "angle": "근거를 바탕으로 한 실용적인 생활 습관",
        "promise": "현실적으로 실천할 수 있는 매일의 습관을 알아봅니다",
        "relevance": "대한민국의 평범한 일상생활",
        "question": "최근 연구는 무엇을 보여 주나요?",
        "advice": "누가 개인별 상담을 받아야 하나요?",
        "title": "현실적인 매일 걷기 습관",
        "narration": "연구에 따르면 규칙적인 걷기는 많은 성인의 건강한 생활에 도움을 줄 수 있습니다.",
        "visual": "나이 든 성인이 차분하게 매일 산책하는 모습",
        "seoTitle": "45세 이후 실천하는 현실적인 매일 걷기",
        "description": "연구에 따르면 규칙적인 걷기는 건강한 생활에 도움을 줄 수 있습니다.",
        "tags": ["건강한 노화", "걷기", "생활 습관"],
        "thumbnail": "매일 걷는 습관",
        "comment": "꾸준히 걷는 데 도움이 되는 습관은 무엇인가요?",
    },
    "ja-JP": {
        "angle": "根拠に基づく実践しやすい生活習慣",
        "promise": "無理なく続けられる毎日の習慣を理解します",
        "relevance": "日本での身近な日常生活",
        "question": "現在の研究では何が示されていますか？",
        "advice": "個別の助言を受けるべき人は誰ですか？",
        "title": "無理なく続ける毎日の散歩習慣",
        "narration": "研究では、定期的な散歩が多くの大人の健やかな生活に役立つ可能性があります。",
        "visual": "年齢を重ねた大人が穏やかに毎日散歩する様子",
        "seoTitle": "45歳から無理なく続ける毎日の散歩習慣",
        "description": "研究では、定期的な散歩が健やかな生活に役立つ可能性があります。",
        "tags": ["健やかな年齢の重ね方", "散歩", "生活習慣"],
        "thumbnail": "毎日の散歩習慣",
        "comment": "散歩を続けるために工夫していることはありますか？",
    },
}


class MatrixProvider:
    name = "deterministic-locale-matrix"

    def generate(self, prompt: PromptEnvelope) -> dict:
        locale = str(
            prompt.payload.get("channel", {}).get("locale")
            or prompt.payload.get("locale")
            or prompt.payload["artifacts"]["idea"]["locale"]
        )
        text = NATIVE[locale]
        if prompt.stage == "idea":
            return {
                "schemaVersion": "localized-idea-v2/v1",
                "locale": locale,
                "angle": text["angle"],
                "audiencePromise": text["promise"],
                "localRelevance": text["relevance"],
                "evidenceQuestions": [text["question"], text["advice"]],
            }
        if prompt.stage == "script":
            return {
                "schemaVersion": "localized-script-v2/v1",
                "locale": locale,
                "title": text["title"],
                "sections": [{"id": "opening", "narration": text["narration"]}],
            }
        if prompt.stage == "scenes":
            return {
                "schemaVersion": "localized-scenes-v2/v1",
                "locale": locale,
                "scenes": [
                    {
                        "id": "opening",
                        "narration": text["narration"],
                        "visualType": "graphic",
                        "visualPrompt": text["visual"],
                        "searchBrief": {
                            "language": "en",
                            "queries": ["older adult daily walking routine"],
                        },
                    }
                ],
            }
        if prompt.stage == "seo":
            return {
                "schemaVersion": "localized-seo-v2/v1",
                "locale": locale,
                "title": text["seoTitle"],
                "description": text["description"],
                "tags": text["tags"],
                "thumbnailText": text["thumbnail"],
                "pinnedComment": text["comment"],
            }
        return {
            "schemaVersion": "localized-qa-v2/v1",
            "locale": locale,
            "verdict": "PASS",
            "failures": [],
        }


def _approve(path: Path, evidence_root: Path) -> None:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    locale = payload["locale"]
    evidence = []
    for name in ("audio", "font", "render", "human-review", "dashboard-lifecycle"):
        relative = Path(locale) / f"{name}.json"
        destination = evidence_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"status":"PASS"}\n', encoding="utf-8")
        evidence.append(str(relative))
    payload["enabled"] = True
    payload["voice"] = {
        "provider": "kokoro",
        "language": "a",
        "voiceId": "qualified-test-voice",
        "speed": 1.0,
    }
    payload["canary"] = {
        "status": "APPROVED",
        "checks": {
            "audio": "PASS",
            "font": "PASS",
            "render": "PASS",
            "humanReview": "PASS",
            "dashboardLifecycle": "PASS",
        },
        "evidence": evidence,
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def test_actual_locale_and_channel_matrix_is_complete_and_fail_closed(tmp_path: Path) -> None:
    locale_registry = LocaleRegistry(LOCALE_ROOT, SCHEMA_ROOT)
    channel_registry = ChannelRegistry(CHANNEL_ROOT, SCHEMA_ROOT, tmp_path / "evidence")

    assert tuple(locale_registry.resolve(locale)["locale"] for locale in SUPPORTED_LOCALES) == (
        *SUPPORTED_LOCALES,
    )
    assert ROLLOUT_ORDER == {
        "en-US": 1,
        "fr-FR": 2,
        "pt-BR": 3,
        "ko-KR": 4,
        "ja-JP": 5,
    }
    assert len(channel_registry.all()) == 5
    assert channel_registry.enabled() == {}
    assert all(channel["voice"] is None for channel in channel_registry.all().values())
    assert all(channel["render"]["concurrency"] == "auto" for channel in channel_registry.all().values())
    assert all(channel["render"]["subtitles"]["enabled"] is False for channel in channel_registry.all().values())
    assert all(channel["content"]["type"] == "long_form" for channel in channel_registry.all().values())


def test_actual_locale_packs_include_native_language_safety_seo_and_fonts() -> None:
    registry = LocaleRegistry(LOCALE_ROOT, SCHEMA_ROOT)
    expected_fonts = {
        "en-US": "Manrope",
        "fr-FR": "Manrope",
        "pt-BR": "Manrope",
        "ko-KR": "Noto Sans KR",
        "ja-JP": "Noto Sans JP",
    }
    legacy_markers = ("vida plena", "suscríbete", "escribe en español")

    for locale in SUPPORTED_LOCALES:
        pack = registry.resolve(locale)
        rendered = json.dumps(pack, ensure_ascii=False).casefold()
        assert expected_fonts[locale] in pack["fonts"]["families"]
        assert all(len(codepoint) in {4, 5, 6} for codepoint in pack["fonts"]["requiredCodepoints"])
        assert pack["medicalSafety"]["softClaims"]
        assert pack["medicalSafety"]["prohibitedClaims"]
        assert pack["seo"]["keywordCues"]
        assert NATIVE[locale]["tags"][0] in pack["seo"]["keywordCues"]
        assert not any(marker in rendered for marker in legacy_markers)


def test_enabled_channel_requires_real_canary_evidence(tmp_path: Path) -> None:
    channels = tmp_path / "channels"
    shutil.copytree(CHANNEL_ROOT, channels)
    evidence = tmp_path / "evidence"
    first = channels / "pending-en-us" / "channel.yaml"
    _approve(first, evidence)
    (evidence / "en-US" / "render.json").unlink()

    with pytest.raises(ContractValidationError, match="missing or unsafe canary evidence") as error:
        ChannelRegistry(channels, SCHEMA_ROOT, evidence)

    assert error.value.code == "INVALID_CANARY_EVIDENCE"


def test_later_locale_cannot_skip_earlier_canary(tmp_path: Path) -> None:
    channels = tmp_path / "channels"
    shutil.copytree(CHANNEL_ROOT, channels)
    evidence = tmp_path / "evidence"
    _approve(channels / "pending-fr-fr" / "channel.yaml", evidence)

    with pytest.raises(ContractValidationError, match="cannot be enabled") as error:
        ChannelRegistry(channels, SCHEMA_ROOT, evidence)

    assert error.value.code == "ROLLOUT_DEPENDENCY_FAILED"


def test_disabled_template_cannot_hide_voice_or_partial_canary(tmp_path: Path) -> None:
    payload = yaml.safe_load(
        (CHANNEL_ROOT / "pending-en-us" / "channel.yaml").read_text(encoding="utf-8")
    )
    payload["voice"] = {
        "provider": "kokoro",
        "language": "a",
        "voiceId": "unqualified",
        "speed": 1.0,
    }
    path = tmp_path / "channel.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ContractValidationError):
        load_channel_config(path, SCHEMA_ROOT)


def test_dashboard_rejects_direct_injection_of_disabled_template(tmp_path: Path) -> None:
    channel = load_channel_config(
        CHANNEL_ROOT / "pending-en-us" / "channel.yaml",
        SCHEMA_ROOT,
    )
    locale_pack = LocaleRegistry(LOCALE_ROOT, SCHEMA_ROOT).resolve("en-US")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "runtime", legacy_jobs_root=legacy)
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db)

    with pytest.raises(ValueError, match="approved enabled channels"):
        DashboardService(
            LocalizedRuntime(paths, queue),
            queue,
            {
                channel["channelId"]: EnabledChannel(
                    channel,
                    locale_pack,
                    CapabilityInventory(
                        media_root=tmp_path / "media",
                        voices=frozenset(),
                        fonts=frozenset(),
                        brand_clips=frozenset(),
                    ),
                )
            },
        )


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_deterministic_fake_provider_reaches_voice_only_final_render(
    locale: str,
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-jobs"
    legacy.mkdir()
    paths = RuntimePaths.build(tmp_path / "runtime-v2", legacy_jobs_root=legacy)
    paths.initialize()
    queue = LocalizedQueue(paths.queue_db)
    channel, _fixture_pack = snapshots(locale)
    locale_pack = LocaleRegistry(LOCALE_ROOT, SCHEMA_ROOT).resolve(locale)
    job_id = f"matrix-{locale.lower()}"
    queue.create_job(
        JobInput(
            job_id=job_id,
            channel_id=channel["channelId"],
            locale=locale,
            topic=NATIVE[locale]["title"],
            channel_snapshot=channel,
            locale_snapshot=locale_pack,
        )
    )
    voices = frozenset(
        {
            (
                str(channel["voice"]["provider"]),
                str(channel["voice"]["language"]),
                str(channel["voice"]["voiceId"]),
            )
        }
    )
    tts = LocalizedTTS(
        {
            "kokoro": FakeTTSBackend(duration_sec=0.1),
            "melo": FakeTTSBackend(duration_sec=0.1),
        },
        VoiceCapabilityRegistry(voices),
    )
    content = LocalizedOrchestrator(
        LocalizedPromptRunner(paths, SCHEMA_ROOT, MatrixProvider()),
        tts,
    )
    clips: dict[str, BrandClip] = {}
    for name, duration in (("intro", 0.1), ("disclaimer", 0.1), ("outro", 0.1)):
        path = tmp_path / "brand" / f"{name}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(MP4)
        clips[name] = BrandClip(path, duration)
    runner = LocalizedMediaOrchestrator(
        content,
        LocalizedAssetPipeline(FakeAssetProvider()),
        clips,
        queue,
        renderer=FakeRenderer(),
    )
    worker = LocalizedWorker(f"matrix-{locale}", paths, queue, runner)

    assert worker.run_once()
    job = queue.get_job(job_id)
    artifacts = queue.list_artifacts(job_id)
    render_props_path = next(
        Path(item["path"]) for item in artifacts if item["name"] == "render-props.json"
    )
    render_props = json.loads(render_props_path.read_text(encoding="utf-8"))

    assert job["status"] == "COMPLETED"
    assert queue.completed_stages(job_id)[-1] == "render"
    assert render_props["locale"] == locale
    assert render_props["audio"]["music"] is None
    assert render_props["render"]["subtitles"]["enabled"] is False
    assert "captions" not in render_props
    assert any(item["name"] == "final.mp4" for item in artifacts)
    assert not any(
        forbidden in item["name"]
        for item in artifacts
        for forbidden in ("whisper", "subtitle", "music", "word_segments")
    )
