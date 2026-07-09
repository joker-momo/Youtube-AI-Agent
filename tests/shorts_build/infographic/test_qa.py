from pathlib import Path

from video_agent.shorts.infographic.qa import qa_poster

PLAN = {"title": "Foods For Eyes", "items": [{"label": "Chía"}, {"label": "Salmón"}]}


def test_pass_when_all_text_present():
    v = qa_poster(Path("x.png"), PLAN, read_text_fn=lambda p: "FOODS FOR EYES chia salmon")
    assert v["verdict"] == "pass"
    assert v["missing"] == []


def test_fail_lists_missing_labels():
    v = qa_poster(Path("x.png"), PLAN, read_text_fn=lambda p: "Foods For Eyes chia")
    assert v["verdict"] == "fail"
    assert "Salmón" in v["missing"]


def test_unavailable_reader_yields_qa_unavailable():
    def boom(p):
        raise RuntimeError("no vision/ocr")

    v = qa_poster(Path("x.png"), PLAN, read_text_fn=boom)
    assert v["verdict"] == "qa_unavailable"
