from __future__ import annotations

from video_agent.assets.providers import normalize_pexels_response, normalize_pixabay_response


def test_normalize_pexels_response_preserves_credit_and_download_url():
    response = {
        "photos": [
            {
                "id": 6793199,
                "url": "https://www.pexels.com/photo/a-woman-sitting-on-bed-6793199/",
                "photographer": "Yaroslav Shuraev",
                "photographer_url": "https://www.pexels.com/@yaroslav-shuraev",
                "width": 6550,
                "height": 4367,
                "alt": "Woman reclining on bed wearing a sleep mask.",
                "src": {"large2x": "https://images.pexels.com/photos/6793199/large2x.jpg"},
            }
        ]
    }

    result = normalize_pexels_response(response)

    assert result[0]["provider"] == "pexels"
    assert result[0]["provider_asset_id"] == "6793199"
    assert result[0]["download_url"] == "https://images.pexels.com/photos/6793199/large2x.jpg"
    assert result[0]["photographer"] == "Yaroslav Shuraev"
    assert result[0]["photographer_url"] == "https://www.pexels.com/@yaroslav-shuraev"
    assert result[0]["attribution"] == "Photo by Yaroslav Shuraev on Pexels"
    assert result[0]["tags"] == ["Woman reclining on bed wearing a sleep mask."]


def test_normalize_pixabay_response_preserves_credit_and_download_url():
    response = {
        "hits": [
            {
                "id": 7009836,
                "pageURL": "https://pixabay.com/photos/cat-sleep-nap-7009836/",
                "fullHDURL": "https://pixabay.com/get/fullhd.jpg",
                "largeImageURL": "https://pixabay.com/get/large.jpg",
                "user": "planet_fox",
                "user_id": 4691618,
                "tags": "sleep, wellness, rest",
                "imageWidth": 6960,
                "imageHeight": 4640,
            }
        ]
    }

    result = normalize_pixabay_response(response)

    assert result[0]["provider"] == "pixabay"
    assert result[0]["provider_asset_id"] == "7009836"
    assert result[0]["download_url"] == "https://pixabay.com/get/fullhd.jpg"
    assert result[0]["source_url"] == "https://pixabay.com/photos/cat-sleep-nap-7009836/"
    assert result[0]["photographer"] == "planet_fox"
    assert result[0]["photographer_url"] == "https://pixabay.com/users/planet_fox-4691618/"
    assert result[0]["attribution"] == "Image by planet_fox from Pixabay"
    assert result[0]["tags"] == ["sleep", "wellness", "rest"]


def test_keywordize_query_splits_hyphens_and_removes_verbs():
    from video_agent.assets.providers import keywordize_query
    q = "Vertical 9:16 sequence-style scene with adult hands closing a laptop"
    res = keywordize_query(q, max_terms=3)
    assert "sequencestyle" not in res
    assert "closing" not in res
    assert res == "adult hands laptop"


from unittest.mock import patch
from video_agent.assets.providers import StockPhotoClient

@patch("video_agent.assets.providers._read_json")
def test_coverr_video_search_retry_fallback(mock_read_json):
    # Mocking first call to return 0 hits, and second call to return 1 hit
    mock_read_json.side_effect = [
        {"hits": []},
        {"hits": [{"id": "v1", "urls": {"mp4": "http://example.com/v1.mp4"}}]}
    ]
    client = StockPhotoClient()
    with patch.dict("os.environ", {"COVERR_API_KEY": "dummy_key"}):
        res = client.search("coverr_video", "bedroom night woman", {"orientation": "portrait"})
    assert len(res["hits"]) == 1
    # Check that it called _read_json twice (first with max_terms=3, then max_terms=2)
    assert mock_read_json.call_count == 2


