from video_agent.shorts import paths


def test_infographic_artifact_names_exist():
    assert paths.SHORT_POSTER_PLAN_FILE == "poster_plan.json"
    assert paths.SHORT_POSTER_QA_FILE == "poster_qa.json"
    assert paths.SHORT_POSTER_IMAGE_NAME == "poster.png"
