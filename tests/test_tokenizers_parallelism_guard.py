"""Regression: importing the local semantic-vision module must disable HF
tokenizers parallelism process-wide. Grounding DINO / SigLIP processors deadlock
on their rayon thread-pool after a fork when TOKENIZERS_PARALLELISM is unset
(encode_batch blocks forever on a semaphore, ~0 CPU), which froze visual_local_qa
mid-render. (bug-356)"""

from __future__ import annotations

import importlib
import os


def test_visual_semantic_import_disables_tokenizers_parallelism() -> None:
    os.environ.pop("TOKENIZERS_PARALLELISM", None)
    import video_agent.shorts.visual_semantic as visual_semantic

    importlib.reload(visual_semantic)  # re-run module body with the var unset
    assert os.environ.get("TOKENIZERS_PARALLELISM") == "false"


def test_explicit_override_is_respected() -> None:
    # setdefault must not clobber an operator's explicit choice.
    os.environ["TOKENIZERS_PARALLELISM"] = "true"
    try:
        import video_agent.shorts.visual_semantic as visual_semantic

        importlib.reload(visual_semantic)
        assert os.environ.get("TOKENIZERS_PARALLELISM") == "true"
    finally:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
