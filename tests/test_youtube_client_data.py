from kontur.connectors.youtube.client import YouTubeClient
from tests.youtube_fake import make_transport


def _client(transport):
    return YouTubeClient(api_key="k", access_token="a", transport=transport, sleep=lambda *_: None)


def test_iter_playlist_items_follows_page_tokens():
    transport, _ = make_transport(playlist_pages=[["v1", "v2"], ["v3"]])
    with _client(transport) as c:
        assert list(c.iter_playlist_items("UUabc")) == ["v1", "v2", "v3"]


def test_videos_batches_by_50():
    ids = [f"v{i}" for i in range(120)]
    vids = [{"id": i, "snippet": {}, "statistics": {}, "contentDetails": {}} for i in ids]
    transport, calls = make_transport(videos=vids)
    with _client(transport) as c:
        out = c.videos(ids)
    assert [v["id"] for v in out] == ids
    # 120 → 3 запроса (50+50+20)
    assert sum(1 for s, *_ in calls if s == "videos") == 3


def test_data_call_carries_api_key_not_bearer():
    transport, calls = make_transport(playlist_pages=[[]])
    with _client(transport) as c:
        list(c.iter_playlist_items("UUabc"))
    _, params, headers = calls[0]
    assert params.get("key") == "k"
    assert "authorization" not in {k.lower() for k in headers}
