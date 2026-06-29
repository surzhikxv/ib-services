from kontur.connectors.youtube.mapping import (
    channel_values, content_values, subscriber_count, uploads_playlist_id,
)

CH = {
    "id": "UCabc",
    "snippet": {"title": "Лапычев", "customUrl": "@lapychev", "country": "RU"},
    "statistics": {"subscriberCount": "1500", "viewCount": "900000", "videoCount": "120"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
}
VIDEO = {
    "id": "vid1",
    "snippet": {"title": "Как начать", "publishedAt": "2026-05-01T10:00:00Z", "description": "d"},
    "statistics": {"viewCount": "320", "likeCount": "40", "commentCount": "5"},
    "contentDetails": {"duration": "PT3M20S"},
}


def test_channel_values_and_helpers():
    cv = channel_values(CH)
    assert cv["platform"] == "youtube"
    assert cv["external_id"] == "UCabc"
    assert cv["title"] == "Лапычев"
    assert cv["url"] == "https://youtube.com/channel/UCabc"
    assert cv["meta"]["subscriberCount"] == "1500"
    assert cv["meta"]["handle"] == "@lapychev"
    assert uploads_playlist_id(CH) == "UUabc"
    assert subscriber_count(CH) == 1500


def test_content_values_lifetime_metrics_and_type():
    c = content_values(VIDEO)
    assert c["external_id"] == "vid1"
    assert c["type"] == "video"               # 3m20s → long-form
    assert c["title"] == "Как начать"
    assert c["url"] == "https://youtube.com/watch?v=vid1"
    assert c["published_at"].year == 2026
    assert c["metrics"] == {"views": 320, "likes": 40, "comments": 5}
    assert c["raw"]["statistics"]["likeCount"] == "40"


def test_content_values_short_under_60s():
    v = {**VIDEO, "id": "s1", "contentDetails": {"duration": "PT45S"}}
    assert content_values(v)["type"] == "short"


def test_content_values_missing_stats_are_none_not_zero():
    v = {"id": "x", "snippet": {"title": "t", "publishedAt": None}, "statistics": {}}
    c = content_values(v)
    assert c["metrics"] == {"views": None, "likes": None, "comments": None}
    assert c["published_at"] is None
