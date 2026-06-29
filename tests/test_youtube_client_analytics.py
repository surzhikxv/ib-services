from kontur.connectors.youtube.client import YouTubeClient
from tests.youtube_fake import make_transport

REPORT = {"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [["2026-06-01", 10]]}


def _client(transport):
    return YouTubeClient(api_key="k", access_token="atok", transport=transport,
                         sleep=lambda *_: None)


def test_report_sends_bearer_and_params():
    transport, calls = make_transport(reports=REPORT)
    with _client(transport) as c:
        out = c.report(start_date="2026-06-01", end_date="2026-06-03",
                       metrics=["views", "likes"], dimensions="day",
                       filters="video==vid1", sort="day")
    assert out["rows"] == [["2026-06-01", 10]]
    seg, params, headers = calls[0]
    assert seg == "reports"
    assert params["ids"] == "channel==MINE"
    assert params["startDate"] == "2026-06-01" and params["endDate"] == "2026-06-03"
    assert params["metrics"] == "views,likes"
    assert params["dimensions"] == "day" and params["filters"] == "video==vid1"
    assert headers.get("authorization") == "Bearer atok"
    assert "key" not in params           # Analytics — по Bearer, не по ключу
