import httpx
import pytest

from kontur.connectors.vk.client import VKClient, VKError
from tests.vk_fake import make_transport, post, reach_row


def _client(transport, **kw):
    return VKClient("tok", transport=transport, sleep=lambda *_: None, **kw)


def test_call_returns_response_unwrapped():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"response": {"ok": 1}}))
    assert _client(t)._call("any.method") == {"ok": 1}


def test_call_raises_vkerror_on_error_body():
    t = httpx.MockTransport(lambda r: httpx.Response(
        200, json={"error": {"error_code": 5, "error_msg": "auth failed"}}))
    with pytest.raises(VKError) as ei:
        _client(t)._call("any.method")
    assert ei.value.code == 5 and "auth failed" in ei.value.msg


def test_call_sends_token_and_version():
    seen = {}

    def handler(r):
        seen.update(dict(r.url.params))
        return httpx.Response(200, json={"response": 1})

    _client(httpx.MockTransport(handler), version="5.199")._call("m", group_id=7)
    assert seen["access_token"] == "tok" and seen["v"] == "5.199" and seen["group_id"] == "7"


def test_call_retries_on_rate_limit_then_succeeds():
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(200, json={"error": {"error_code": 6, "error_msg": "too many"}})
        return httpx.Response(200, json={"response": "ok"})

    assert _client(httpx.MockTransport(handler))._call("m") == "ok"
    assert state["n"] == 2


def test_call_gives_up_after_max_retries():
    def handler(r):
        return httpx.Response(200, json={"error": {"error_code": 6, "error_msg": "too many"}})

    with pytest.raises(VKError):
        _client(httpx.MockTransport(handler), max_retries=2)._call("m")


def test_group_by_id_unwraps_groups_dict():
    t, _ = make_transport(group={"id": 229, "name": "G", "screen_name": "g"}, wall_pages=[[]])
    g = _client(t).group_by_id(229)
    assert g["id"] == 229 and g["name"] == "G"


def test_group_by_id_handles_legacy_array_shape():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"response": [{"id": 1}]}))
    assert _client(t).group_by_id(1)["id"] == 1


def test_iter_wall_paginates_and_stops():
    page0 = [post(i) for i in range(100, 0, -1)]  # 100 постов
    page1 = [post(i) for i in range(-1, -4, -1)]  # ещё 3
    t, calls = make_transport(group={"id": 1}, wall_pages=[page0, page1])
    ids = [p["id"] for p in _client(t).iter_wall(-1)]
    assert len(ids) == 103
    offsets = [int(p["offset"]) for m, p in calls if m == "wall.get"]
    assert offsets == [0, 100]  # ровно две страницы, без лишнего запроса
    assert all(p.get("filter") == "owner" for m, p in calls if m == "wall.get")


def test_post_reach_joins_ids_with_comma_not_repeated_keys():
    captured = {}

    def handler(r):
        if r.url.path.endswith("stats.getPostReach"):
            captured["post_ids"] = dict(r.url.params)["post_ids"]
            captured["query"] = str(r.url.query)
            return httpx.Response(200, json={"response": [
                {"post_id": 121, "reach_total": 5}, {"post_id": 9, "reach_total": 582}]})
        return httpx.Response(200, json={"response": []})

    out = _client(httpx.MockTransport(handler)).post_reach(-1, [121, 9])
    assert captured["post_ids"] == "121,9"
    assert "post_ids=121%2C9" in captured["query"] or "post_ids=121,9" in captured["query"]
    assert out[121]["reach_total"] == 5 and out[9]["reach_total"] == 582


def test_post_reach_dedups_and_batches():
    reach = [reach_row(i, i * 10) for i in range(1, 151)]
    batches = []

    def handler(r):
        if r.url.path.endswith("stats.getPostReach"):
            ids = dict(r.url.params)["post_ids"].split(",")
            batches.append(len(ids))
            rows = [x for x in reach if x["post_id"] in {int(i) for i in ids}]
            return httpx.Response(200, json={"response": rows})
        return httpx.Response(200, json={"response": []})

    ids = list(range(1, 151)) + [1, 2, 3]  # дубли закрепа
    out = _client(httpx.MockTransport(handler)).post_reach(-1, ids)
    assert batches == [30, 30, 30, 30, 30]  # дедуп → 150 уникальных → 5 батчей по 30 (лимит VK)
    assert len(out) == 150


def test_post_reach_best_effort_skips_failed_batch():
    def handler(r):
        return httpx.Response(200, json={"error": {"error_code": 8, "error_msg": "boom"}})

    assert _client(httpx.MockTransport(handler)).post_reach(-1, [1, 2]) == {}


def test_group_stats_uses_timestamps_not_deprecated_dates():
    seen = {}

    def handler(r):
        seen.update(dict(r.url.params))
        return httpx.Response(200, json={"response": []})

    _client(httpx.MockTransport(handler)).group_stats(229, timestamp_from=1779840000, timestamp_to=1782432000)
    assert seen["timestamp_from"] == "1779840000" and seen["timestamp_to"] == "1782432000"
    assert "date_from" not in seen and "date_to" not in seen  # VK 5.86+ их выпилил
