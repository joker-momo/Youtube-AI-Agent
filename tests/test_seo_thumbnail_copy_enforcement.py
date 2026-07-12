"""Live-job regression: every generated thumbnail variant must meet the copy contract.

Job vida-sana-4-alimentos-...-20260712-151721 selected a good primary
variant, but Gemini SEO QA passed two secondary variants that were then eligible
for expensive thumbnail image generation:

* ``QUÉ ELEGIR, MENOS SAL`` omits what the viewer should choose;
* ``SAL OCULTA EN TU PLATO`` names a topic but offers no action/outcome.

Ranking the best candidate is insufficient because the pipeline renders all
three variants. Deterministic QA must reject/rework the whole set.
"""

from __future__ import annotations

import json

from video_agent.orchestrator.stages.seo import _enforce_seo_language_qa
from video_agent.seo.title_scorer import score_variant

GOOD_VARIANTS = [
    {
        "title": "No es el salero: cómo cuidar el corazón después de los 45",
        "thumbnail_text": "MENOS SAL, CUIDA TU CORAZÓN",
    },
    {
        "title": "Alimentos para el corazón: qué elegir y reducir la sal",
        "thumbnail_text": "ELIGE ALIMENTOS CON MENOS SAL",
    },
    {
        "title": "La sal oculta: alimentos para cuidar el corazón",
        "thumbnail_text": "EVITA LA SAL OCULTA DEL PLATO",
    },
]


def _write_job(tmp_path, variants):
    job_dir = tmp_path / "job"
    (job_dir / "json").mkdir(parents=True)
    (job_dir / "json" / "seo.json").write_text(
        json.dumps(
            {
                "language": "es-ES",
                "title": variants[0]["title"],
                "thumbnail_text": variants[0]["thumbnail_text"],
                "title_variants": variants,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    channel_path = tmp_path / "channel.yaml"
    channel_path.write_text(
        "channel:\n  id: vida-plena-45\naudience:\n  language: es-ES\nseo:\n  language: es-ES\n",
        encoding="utf-8",
    )
    qa_path = tmp_path / "seo_qa.json"
    qa_payload = {
        "artifact": "seo",
        "verdict": "PASS",
        "issues": [],
        "required_changes": [],
        "scores": {"channel_fit": 5},
    }
    qa_path.write_text(json.dumps(qa_payload), encoding="utf-8")
    return job_dir, channel_path, qa_path, qa_payload


def test_context_free_title_overlap_cannot_rescue_missing_thumbnail_object():
    result = score_variant(
        {
            "title": "Alimentos para el corazón: qué elegir y cómo reducir la sal",
            "thumbnail_text": "QUÉ ELEGIR, MENOS SAL",
        }
    )
    detail = result["breakdown"]["thumbnail_detail"]

    assert detail["vagueness_penalty"] > 0
    assert detail["standalone_value_score"] < 14


def test_seo_qa_reworks_when_any_secondary_thumbnail_variant_is_invalid(tmp_path):
    variants = [
        GOOD_VARIANTS[0],
        {
            "title": "Alimentos para el corazón: qué elegir y cómo reducir la sal",
            "thumbnail_text": "QUÉ ELEGIR, MENOS SAL",
        },
        {
            "title": "La sal oculta en tu plato: 4 alimentos para el corazón",
            "thumbnail_text": "SAL OCULTA EN TU PLATO",
        },
    ]
    job_dir, channel_path, qa_path, qa_payload = _write_job(tmp_path, variants)

    _enforce_seo_language_qa(
        job_dir,
        qa_path,
        qa_payload,
        channel_path=channel_path,
    )

    updated = json.loads(qa_path.read_text(encoding="utf-8"))
    assert updated["verdict"] == "NEEDS_REWORK"
    joined = " ".join(updated["issues"] + updated["required_changes"])
    assert "title_variants[1]" in joined
    assert "title_variants[2]" in joined
    assert "standalone" in joined.lower()


def test_seo_qa_keeps_pass_when_all_thumbnail_variants_are_valid(tmp_path):
    job_dir, channel_path, qa_path, qa_payload = _write_job(tmp_path, GOOD_VARIANTS)

    _enforce_seo_language_qa(
        job_dir,
        qa_path,
        qa_payload,
        channel_path=channel_path,
    )

    updated = json.loads(qa_path.read_text(encoding="utf-8"))
    assert updated["verdict"] == "PASS"
    assert updated["issues"] == []

