from datetime import date

from kontur.connectors.youtube.mapping import channel_metric_rows, content_metric_rows

CH_REPORT = {
    "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"},
                      {"name": "comments"}, {"name": "shares"}, {"name": "subscribersGained"},
                      {"name": "subscribersLost"}, {"name": "estimatedMinutesWatched"}],
    "rows": [["2026-06-01", 100, 9, 3, 1, 5, 1, 220]],
}
VID_REPORT = {
    "columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "likes"},
                      {"name": "comments"}, {"name": "shares"},
                      {"name": "averageViewPercentage"}, {"name": "estimatedMinutesWatched"}],
    "rows": [["2026-06-01", 50, 4, 1, 0, 38.5, 90]],
}


def test_channel_metric_rows_typed_and_raw():
    rows = channel_metric_rows(CH_REPORT, subscriber_count=1500)
    assert len(rows) == 1
    r = rows[0]
    assert r["snapshot_date"] == date(2026, 6, 1)
    assert r["video_views"] == 100 and r["likes"] == 9 and r["shares"] == 1
    assert r["followers_gained"] == 5
    assert r["followers"] == 1500          # из Data API channels.list
    assert r["profile_views"] is None and r["reach"] is None   # у YouTube нет аналога
    # немапленное — в raw, без дублей типизированных
    assert r["raw"]["subscribersLost"] == 1
    assert r["raw"]["estimatedMinutesWatched"] == 220
    assert "views" not in r["raw"]
    assert "subscribersGained" not in r["raw"]   # source of typed followers_gained, not duplicated


def test_content_metric_rows_typed_and_raw():
    rows = content_metric_rows(VID_REPORT)
    r = rows[0]
    assert r["snapshot_date"] == date(2026, 6, 1)
    assert r["views"] == 50 and r["likes"] == 4 and r["comments"] == 1 and r["shares"] == 0
    assert r["reach"] is None and r["saves"] is None
    assert r["raw"]["averageViewPercentage"] == 38.5
    assert r["raw"]["estimatedMinutesWatched"] == 90
