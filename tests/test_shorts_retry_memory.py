# tests/test_shorts_retry_memory.py
from video_agent.shorts.retry_memory import make_stable_issue_id, RetryIssue

def test_stable_id_normalization():
    detail1 = "CTA scene s07 must be <= 2.8 sec"
    id1 = make_stable_issue_id("scene_validation", "s07", "duration", detail1)
    
    detail2 = "CTA scene s07 must be <= 5.2 sec"
    id2 = make_stable_issue_id("scene_validation", "s07", "duration", detail2)
    
    assert id1 == id2
    assert id1 == "scene_validation:s07:duration:cta_scene_scene_id_must_be_n_sec"
