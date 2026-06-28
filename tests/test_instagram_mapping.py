from datetime import datetime, timezone

from kontur.connectors.instagram.mapping import (
    channel_values, content_metric_values, content_values,
    channel_metric_values, parse_insights, parse_ts,
)


def _insight(name, value=None, breakdowns=None, series=None):
    item = {"name": name, "period": "lifetime", "title": name, "description": ""}
    if series is not None:
        item["values"] = [{"value": v} for v in series]
    else:
        item["total_value"] = {"value": value, "breakdowns": breakdowns or []}
    return item


def test_parse_insights_total_value_and_empty():
    data = [_insight("reach", 100), _insight("views", None), _insight("likes", series=[7])]
    parsed = parse_insights(data)
    assert parsed["reach"]["value"] == 100
    assert parsed["views"]["value"] is None          # empty stays None, never 0
    assert parsed["likes"]["value"] == 7


def test_parse_insights_ignores_nameless_and_empty_list():
    assert parse_insights([]) == {}
    assert parse_insights([{"period": "day"}]) == {}


def test_parse_ts_handles_meta_offset_format():
    assert parse_ts("2026-01-15T12:34:56+0000") == datetime(2026, 1, 15, 12, 34, 56, tzinfo=timezone.utc)
    assert parse_ts(None) is None


def test_channel_values_from_me():
    me = {"user_id": "17841400000000000", "username": "lapychev", "account_type": "Media_Creator",
          "followers_count": 1200, "follows_count": 80, "media_count": 340, "name": "Лапычев"}
    cv = channel_values(me)
    assert cv["platform"] == "instagram"
    assert cv["external_id"] == "17841400000000000"
    assert cv["url"] == "https://instagram.com/lapychev"
    assert cv["meta"]["account_type"] == "Media_Creator"
    assert cv["meta"]["followers_count"] == 1200


def test_content_values_reel():
    media = {"id": "1789", "media_product_type": "REELS", "media_type": "VIDEO",
             "caption": "x" * 600, "permalink": "https://instagram.com/reel/abc",
             "timestamp": "2026-02-01T08:00:00+0000", "like_count": 50, "comments_count": 4}
    insights = {"reach": {"value": 900, "breakdowns": []}, "views": {"value": 1500, "breakdowns": []},
                "likes": {"value": 50, "breakdowns": []}, "saved": {"value": 12, "breakdowns": []}}
    c = content_values(media, insights)
    assert c["external_id"] == "1789"
    assert c["type"] == "REELS"
    assert len(c["title"]) == 500                     # caption truncated
    assert c["url"] == "https://instagram.com/reel/abc"
    assert c["published_at"] == datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)
    assert c["metrics"] == {"reach": 900, "views": 1500, "likes": 50,
                            "comments": None, "shares": None, "saves": 12}
    assert c["raw"]["media_type"] == "VIDEO"


def test_content_metric_values_typed_plus_raw():
    insights = {"reach": {"value": 900, "breakdowns": []},
                "views": {"value": 1500, "breakdowns": []},
                "saved": {"value": 12, "breakdowns": []},
                "ig_reels_avg_watch_time": {"value": 3400, "breakdowns": []},
                "total_interactions": {"value": 66, "breakdowns": []}}
    m = content_metric_values(insights)
    assert m["views"] == 1500 and m["reach"] == 900 and m["saves"] == 12
    assert m["comments"] is None                       # absent → None
    assert m["raw"]["ig_reels_avg_watch_time"]["value"] == 3400
    assert m["raw"]["total_interactions"]["value"] == 66
    assert "reach" not in m["raw"]                      # typed metrics not duplicated into raw


def test_channel_metric_values_typed_plus_demographics():
    me = {"followers_count": 1200}
    insights = {"reach": {"value": 800, "breakdowns": []},
                "views": {"value": 5000, "breakdowns": []},
                "likes": {"value": 300, "breakdowns": []},
                "follows_and_unfollows": {"value": 25, "breakdowns": []}}
    demo = {"follower_demographics": {"country": {"RU": 600, "KZ": 200}}}
    cm = channel_metric_values(me, insights, demo)
    assert cm["followers"] == 1200 and cm["reach"] == 800 and cm["likes"] == 300
    assert cm["followers_gained"] == 25
    assert cm["video_views"] is None and cm["profile_views"] is None   # not overloaded for IG
    assert cm["raw"]["views"]["value"] == 5000                         # account views → raw
    assert cm["raw"]["demographics"]["follower_demographics"]["country"]["RU"] == 600
