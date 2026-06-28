import httpx

from kontur.connectors.instagram.client import InstagramClient


def _value_item(name, value):
    return {"name": name, "period": "lifetime", "total_value": {"value": value, "breakdowns": []}}


def make_insights_transport(*, per_metric):
    """per_metric: {metric_name: value}. Combined calls 400 if ANY metric is in `bad`."""
    calls = []

    def handler(request):
        seg = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        params = dict(request.url.params)
        calls.append((seg, params))
        if seg != "insights":
            return httpx.Response(200, json={"error": {"code": 100, "message": "x"}})
        metrics = params["metric"].split(",")
        data = []
        for m in metrics:
            if m not in per_metric:               # unsupported metric → whole call errors
                return httpx.Response(200, json={"error": {"code": 100,
                                     "message": "An unknown error has occurred."}})
            data.append(_value_item(m, per_metric[m]))
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(handler), calls


def test_media_insights_combined_ok():
    per = {m: 1 for m in ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
                          "total_interactions", "follows", "profile_visits", "profile_activity"]}
    transport, calls = make_insights_transport(per_metric=per)
    c = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    parsed = c.media_insights("999", "FEED")
    assert parsed["reach"]["value"] == 1
    assert sum(1 for seg, _ in calls if seg == "insights") == 1   # one combined call


def test_media_insights_falls_back_per_metric_on_bad():
    # 'profile_activity' unsupported → combined call fails → per-metric fallback isolates it
    per = {m: 2 for m in ["reach", "views", "likes", "comments", "shares", "saved", "reposts",
                          "total_interactions", "follows", "profile_visits"]}
    transport, calls = make_insights_transport(per_metric=per)
    c = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    parsed = c.media_insights("999", "FEED")
    assert parsed["reach"]["value"] == 2
    assert "profile_activity" not in parsed          # bad metric dropped, others survive
    assert sum(1 for seg, _ in calls if seg == "insights") >= 2   # combined + fallbacks


def test_account_insights_passes_window():
    per = {m: 3 for m in ["reach", "views", "accounts_engaged", "total_interactions", "likes",
                          "comments", "saves", "shares", "reposts", "replies",
                          "profile_links_taps", "follows_and_unfollows"]}
    transport, calls = make_insights_transport(per_metric=per)
    c = InstagramClient("tok", transport=transport, sleep=lambda *_: None)
    parsed = c.account_insights("123", since=1700000000, until=1700086400)
    assert parsed["reach"]["value"] == 3
    seg, params = next((s, p) for s, p in calls if s == "insights")
    assert params["since"] == "1700000000" and params["metric_type"] == "total_value"
