from datetime import timezone

from kontur.connectors.vk.mapping import (
    attachment_type,
    channel_values,
    content_values,
    metric_values,
)
from tests.vk_fake import post, reach_row


def test_channel_values_maps_fields_and_url():
    g = {"id": 229, "name": "ЛАПЫЧЕВ", "screen_name": "s.lapychev",
         "members_count": 56, "activity": "ЗОЖ"}
    cv = channel_values(g)
    assert cv["platform"] == "vk" and cv["external_id"] == "229"
    assert cv["title"] == "ЛАПЫЧЕВ" and cv["url"] == "https://vk.com/s.lapychev"
    assert cv["meta"]["members_count"] == 56 and cv["meta"]["activity"] == "ЗОЖ"


def test_channel_values_falls_back_to_club_id_without_screen_name():
    assert channel_values({"id": 42, "name": "X"})["url"] == "https://vk.com/club42"


def test_attachment_type():
    assert attachment_type(post(1, attachments=[{"type": "video"}])) == "video"
    assert attachment_type(post(1, attachments=[{"type": "photo"}])) == "photo"
    assert attachment_type(post(1)) == "post"


def test_content_values_builds_id_url_title_published():
    p = post(9, views=455, likes=7, comments=2, reposts=1, text="Привет " * 50, date=1_741_273_986)
    c = content_values(p, -229, reach_row(9, 582))
    assert c["external_id"] == "-229_9"
    assert c["url"] == "https://vk.com/wall-229_9"
    assert len(c["title"]) == 200  # обрезка
    assert c["published_at"].tzinfo == timezone.utc
    assert c["metrics"] == {"views": 455, "likes": 7, "comments": 2, "shares": 1, "reach": 582}
    assert c["type"] == "post"


def test_content_values_handles_missing_views_and_text():
    p = post(5)  # без views, без text
    c = content_values(p, -1, None)
    assert c["title"] is None
    assert c["metrics"]["views"] is None and c["metrics"]["reach"] is None


def test_metric_values_includes_saves_none_and_raw_reach():
    p = post(9, views=455, likes=7, comments=2, reposts=1)
    r = reach_row(9, 582, subscribers=445, viral=137)
    m = metric_values(p, r)
    assert m["views"] == 455 and m["likes"] == 7 and m["shares"] == 1 and m["reach"] == 582
    assert m["saves"] is None and m["raw"]["reach_subscribers"] == 445


def test_metric_values_reach_none_when_missing():
    assert metric_values(post(1, views=1), None)["reach"] is None
