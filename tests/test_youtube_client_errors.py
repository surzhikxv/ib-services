from kontur.connectors.youtube.client import (
    YouTubeClient, YouTubeError, YouTubeQuotaExceeded,
)
from tests.youtube_fake import make_transport


def _client(transport, **kw):
    return YouTubeClient(api_key="k", access_token="a", transport=transport,
                         sleep=lambda *_: None, max_retries=3, **kw)


def test_quota_exceeded_raises_specific():
    transport, _ = make_transport(errors={"channels": {"status": 403,
                                  "reason": "quotaExceeded", "message": "out"}})
    with _client(transport) as c:
        try:
            c.channel("UCabc")
            assert False
        except YouTubeQuotaExceeded as e:
            assert e.status == 403 and e.reason == "quotaExceeded"


def test_rate_limit_retries_then_succeeds():
    # первый ответ — rateLimitExceeded, второй — нормальный канал
    transport, calls = make_transport(
        channels={"id": "UCabc", "snippet": {}, "statistics": {}, "contentDetails": {}},
        errors={"channels": [{"status": 403, "reason": "rateLimitExceeded"}]})
    with _client(transport) as c:
        ch = c.channel("UCabc")
    assert ch["id"] == "UCabc"
    assert sum(1 for s, *_ in calls if s == "channels") == 2   # повтор был


def test_other_error_raises_generic():
    transport, _ = make_transport(errors={"channels": {"status": 400,
                                  "reason": "badRequest", "message": "bad"}})
    with _client(transport) as c:
        try:
            c.channel("UCabc")
            assert False
        except YouTubeQuotaExceeded:
            assert False, "не должно быть quota"
        except YouTubeError as e:
            assert e.status == 400 and e.reason == "badRequest"


def test_status_fallback_resource_exhausted_raises_quota():
    import httpx
    transport = httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": {"code": 403, "status": "RESOURCE_EXHAUSTED"}}))
    with YouTubeClient(api_key="k", access_token="a", transport=transport, sleep=lambda *_: None) as c:
        try:
            c.channel("UCabc")
            assert False, "expected quota"
        except YouTubeQuotaExceeded as e:
            assert e.reason == "RESOURCE_EXHAUSTED"


def test_non_json_5xx_retries_then_raises():
    import httpx
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="<html>502 Bad Gateway</html>")
    transport = httpx.MockTransport(handler)
    with YouTubeClient(api_key="k", access_token="a", transport=transport,
                       sleep=lambda *_: None, max_retries=2) as c:
        try:
            c.channel("UCabc")
            assert False, "expected YouTubeError"
        except YouTubeError as e:
            assert e.reason == "non-json"
    assert calls["n"] == 3   # 1 initial + 2 retries
