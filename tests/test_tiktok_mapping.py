"""Маппинг/ридер TikTok на реальных формах ответа /aweme/v2/data/insight/."""
import json
import urllib.parse
from datetime import date, timezone

from kontur.connectors.tiktok import mapping, reader


def _url(*reqs):
    tr = urllib.parse.quote(json.dumps([{"insigh_type": t, "aweme_id": a} for t, a in reqs]))
    return f"https://www.tiktok.com/aweme/v2/data/insight/?aid=1988&type_requests={tr}"


# --- два вызова одного видео: вкладки «Обзор» и «Зрители» ------------------
OVERVIEW_CALL = {
    "url": _url(("video_info", "777"), ("video_per_duration_realtime", "777")),
    "json": {
        "status_code": 0,
        "video_info": {
            "aweme_id": "777", "aweme_type": 0, "create_time": 1782216152,
            "desc": "Тест ДЦП", "video": {"duration": 31347},
            "author": {"uid": "7362975467459380230", "unique_id": "lapychevdcp",
                       "nickname": "ТГК slapychev", "sec_uid": "MS4wAAA"},
            "statistics": {"play_count": 748, "digg_count": 68, "comment_count": 1,
                           "share_count": 3, "collect_count": 8},
        },
        "video_per_duration_realtime": {"is_after_7d": False, "value": {"status": 0, "value": 10.23}},
        "video_total_duration_realtime": {"value": {"status": 0, "value": 22753}},
        "video_finish_rate_realtime": {"value": {"status": 0, "value": 0.036}},
        "realtime_new_followers": {"value": {"status": 0, "value": 2}},
        "realtime_total_video_views": {"value": {"status": 0, "value": 748}},
        "video_traffic_source_percent_realtime": {"value": {"status": 0, "value": [
            {"key": "For You", "value": 0.843}, {"key": "Following", "value": 0.05}]}},
        "item_search_terms": {"status": 0, "value": [{"key": "Дцп", "value": 0.235}]},
        "video_retention_rate_realtime": {"value": {"list": [
            {"timestamp": "0", "value": 1.0}, {"timestamp": "1", "value": 0.42}],
            "peak_value": 1000, "status": 0}},
        "video_like_distribution_realtime": {"value": {"list": [{"timestamp": "0", "value": 0.72}], "status": 0}},
        "realtime_total_play_time_history": {"total": 22753, "status": 0,
                                             "list": [{"key": "1781791200", "value": 12811.3}]},
        "video_uv_before": {"status": 2},  # данных нет → не должно всплыть
    },
}
AUDIENCE_CALL = {
    "url": _url(("video_uv", "777"), ("video_viewer_age_percent_realtime", "777")),
    "json": {
        "status_code": 0,
        "video_uv": {"status": 0, "value": 498},
        "video_viewer_new_viewer_percent": {"status": 0, "value": 0.21},
        "video_viewer_return_viewer_percent": {"status": 0, "value": 0.79},
        "video_viewer_follower_percent_realtime": {"value": {"status": 0, "value": 0.35}},
        "video_viewer_nonfollower_percent_realtime": {"value": {"status": 0, "value": 0.65}},
        "video_viewer_age_percent_realtime": {"value": {"status": 0, "value": [
            {"key": "18-24", "value": 0.47}, {"key": "25-34", "value": 0.36}]}},
        "video_viewer_gender_percent_realtime": {"value": {"status": 0, "value": [
            {"key": "male_vv", "value": 0.52}, {"key": "female_vv", "value": 0.48}]}},
        "video_viewer_location_percent_realtime": {"value": {"status": 0, "country_percent_list": [
            {"country_name": "BY", "country_vv_percent": 0.34},
            {"country_name": "UA", "country_vv_percent": 0.15}]}},
    },
}


# --- item_list: каталог постов (перечисление + базовые счётчики строками) ----
# 777 — то же видео, что в insight (богатое перекроет базовое); 888 — без обхода
# (только базовые счётчики); 999 — фотопост (duration 0 → type photo).
ITEM_LIST_CALL = {
    "url": "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/?aid=1988&count=50",
    "json": {
        "cursor": 50, "has_more": True, "status_code": 0,
        "item_list": [
            {"item_id": "777", "item_type": 1, "desc": "Тест ДЦП", "create_time": "1782216152",
             "duration": 31347, "play_count": "748", "like_count": "68", "comment_count": "1",
             "share_count": "3", "favorite_count": "8"},
            {"item_id": "888", "item_type": 1, "desc": "Видео без обхода", "create_time": "1750950882",
             "duration": 33967, "play_count": "81088", "like_count": "2997", "comment_count": "129",
             "share_count": "261", "favorite_count": "251"},
            {"item_id": "999", "item_type": 1, "desc": "Фотопост", "create_time": "1750000000",
             "duration": 0, "play_count": "500", "like_count": "10", "comment_count": "0",
             "share_count": "0", "favorite_count": "2"},
        ],
    },
}


def _merged():
    _, by = reader.parse_capture([OVERVIEW_CALL, AUDIENCE_CALL])
    return by["777"]


def test_parse_capture_folds_item_list_into_catalog():
    _, by = reader.parse_capture([OVERVIEW_CALL, AUDIENCE_CALL, ITEM_LIST_CALL])
    assert set(by) == {"777", "888", "999"}      # перечисление = весь каталог
    assert "video_info" in by["777"] and "_catalog" in by["777"]  # insight + каталог
    assert set(by["888"]) == {"_catalog"}        # видео без обхода — только каталог


def test_content_values_catalog_only_video():
    _, by = reader.parse_capture([ITEM_LIST_CALL])
    c = mapping.content_values("888", by["888"], unique="lapychevdcp")
    assert c["external_id"] == "888" and c["type"] == "video"
    assert c["url"] == "https://www.tiktok.com/@lapychevdcp/video/888"
    assert c["title"] == "Видео без обхода"
    assert c["published_at"].tzinfo == timezone.utc
    assert c["raw"]["duration_ms"] == 33967
    # строковые счётчики каталога приведены к int; reach в каталоге нет
    assert c["metrics"] == {"views": 81088, "reach": None, "likes": 2997,
                            "comments": 129, "shares": 261, "saves": 251}


def test_content_type_photo_when_duration_zero():
    _, by = reader.parse_capture([ITEM_LIST_CALL])
    c = mapping.content_values("999", by["999"], unique="lapychevdcp")
    assert c["type"] == "photo"
    assert c["url"] == "https://www.tiktok.com/@lapychevdcp/photo/999"


def test_insight_overrides_catalog_for_walked_video():
    _, by = reader.parse_capture([OVERVIEW_CALL, AUDIENCE_CALL, ITEM_LIST_CALL])
    c = mapping.content_values("777", by["777"])  # author из insight → unique не нужен
    assert c["url"] == "https://www.tiktok.com/@lapychevdcp/video/777"
    assert c["raw"]["duration_ms"] == 31347       # из video.duration, не из каталога
    assert c["metrics"]["reach"] == 498           # охват только из insight
    m = mapping.metric_values(by["777"])
    assert m["raw"]["traffic_sources"] == {"For You": 0.843, "Following": 0.05}


def test_metric_values_catalog_only_baseline_no_rich():
    _, by = reader.parse_capture([ITEM_LIST_CALL])
    m = mapping.metric_values(by["888"])
    assert (m["views"], m["likes"], m["saves"]) == (81088, 2997, 251)
    assert m["reach"] is None and m["raw"] == {}  # богатого нет, только базовые счётчики


def test_parse_capture_groups_and_merges_by_aweme():
    author, by = reader.parse_capture([OVERVIEW_CALL, AUDIENCE_CALL])
    assert set(by) == {"777"}
    m = by["777"]
    assert "video_info" in m and "video_uv" in m  # слились вызовы обеих вкладок
    assert author["uid"] == "7362975467459380230"


def test_content_values_id_url_type_published():
    c = mapping.content_values("777", _merged())
    assert c["external_id"] == "777" and c["type"] == "video"
    assert c["url"] == "https://www.tiktok.com/@lapychevdcp/video/777"
    assert c["title"] == "Тест ДЦП"
    assert c["published_at"].tzinfo == timezone.utc
    assert c["raw"]["duration_ms"] == 31347
    assert c["metrics"] == {"views": 748, "reach": 498, "likes": 68,
                            "comments": 1, "shares": 3, "saves": 8}


def test_metric_values_typed_columns():
    m = mapping.metric_values(_merged())
    assert (m["views"], m["reach"], m["likes"], m["comments"], m["shares"], m["saves"]) \
        == (748, 498, 68, 1, 3, 8)


def test_metric_values_rich_raw():
    raw = mapping.metric_values(_merged())["raw"]
    assert raw["avg_watch_s"] == 10.23 and raw["total_watch_s"] == 22753
    assert raw["finish_rate"] == 0.036 and raw["new_followers"] == 2
    assert raw["traffic_sources"] == {"For You": 0.843, "Following": 0.05}
    assert raw["search_terms"] == {"Дцп": 0.235}
    assert raw["retention"] == [[0, 1.0], [1, 0.42]]
    assert raw["likes_timeline"] == [[0, 0.72]]
    assert raw["history"]["play_time_s"]["total"] == 22753


def test_metric_values_audience_block():
    aud = mapping.metric_values(_merged())["raw"]["audience"]
    assert aud["new_viewer"] == 0.21 and aud["return_viewer"] == 0.79
    assert aud["follower"] == 0.35 and aud["non_follower"] == 0.65
    assert aud["age"] == {"18-24": 0.47, "25-34": 0.36}
    assert aud["gender"] == {"male_vv": 0.52, "female_vv": 0.48}
    assert aud["geo"] == {"BY": 0.34, "UA": 0.15}


def test_status2_suppressed_to_none():
    assert mapping._scalar({"status": 2}) is None
    assert mapping._scalar({"value": {"status": 2}}) is None
    # video_uv_before status:2 не должен появиться в raw
    assert "video_uv_before" not in json.dumps(mapping.metric_values(_merged())["raw"])


def test_channel_values():
    author, _ = reader.parse_capture([OVERVIEW_CALL, AUDIENCE_CALL])
    cv = mapping.channel_values(author)
    assert cv["platform"] == "tiktok" and cv["external_id"] == "7362975467459380230"
    assert cv["title"] == "ТГК slapychev"
    assert cv["url"] == "https://www.tiktok.com/@lapychevdcp"
    assert cv["meta"]["unique_id"] == "lapychevdcp"


# --- Overview.csv (RU-даты прописью без года) ------------------------------
def test_parse_overview_ru_dates_and_ints():
    csv_text = (
        '"Date","Video Views","Profile Views","Likes","Comments","Shares"\n'
        '"28 апреля","342","5","7","0","0"\n'
        '"1 мая","330","3","13","-1","1"\n'
    )
    rows = reader.parse_overview(csv_text, year=2026)
    assert rows[0]["snapshot_date"] == date(2026, 4, 28)
    assert rows[0]["video_views"] == 342 and rows[0]["profile_views"] == 5
    assert rows[1]["snapshot_date"] == date(2026, 5, 1)
    assert rows[1]["comments"] == -1  # дневная нетто-дельта бывает отрицательной


def test_parse_overview_strips_utf8_bom():
    # нативный экспорт TikTok начинается с UTF-8 BOM → первый ключ не должен ломаться
    csv_text = '﻿"Date","Video Views","Profile Views","Likes","Comments","Shares"\n"28 апреля","342","5","7","0","0"\n'
    rows = reader.parse_overview(csv_text, year=2026)
    assert len(rows) == 1 and rows[0]["video_views"] == 342


def test_parse_overview_year_rollover_december_january():
    csv_text = (
        '"Date","Video Views","Profile Views","Likes","Comments","Shares"\n'
        '"30 декабря","10","1","1","0","0"\n'
        '"2 января","20","2","2","0","0"\n'
    )
    rows = reader.parse_overview(csv_text, year=2025)
    assert rows[0]["snapshot_date"] == date(2025, 12, 30)
    assert rows[1]["snapshot_date"] == date(2026, 1, 2)
