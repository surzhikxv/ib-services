"""TDD: BotHelpClient — авто-обновление токена и курсорная пагинация.

Сеть замокана через httpx.MockTransport — тесты детерминированы и быстры.
Формат ответов повторяет живой API (снято 2026-06-15):
  /oauth2/token -> {access_token, expires_in}
  /v1/subscribers/ -> {data:[...], paging:{cursor:{after:N}, next:"after=N"}}; конец -> paging:null
  /v1/bots/{ref}/steps -> [ {...}, ... ]  (голый список)
"""
import httpx
import pytest

from kontur.connectors.bothelp.client import BotHelpClient


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def make_client(handler, clock=None):
    transport = httpx.MockTransport(handler)
    return BotHelpClient(
        client_id="cid",
        client_secret="secret",
        oauth_url="https://oauth.test/oauth2/token",
        api_base="https://api.test",
        transport=transport,
        clock=clock or FakeClock(),
    )


def _token_response(expires_in=3600, token="tok-1"):
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


def test_fetches_token_once_and_reuses_within_expiry():
    calls = {"oauth": 0, "api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["oauth"] += 1
            return _token_response(token=f"tok-{calls['oauth']}")
        calls["api"] += 1
        assert request.headers["Authorization"] == "Bearer tok-1"
        return httpx.Response(200, json=[{"ok": True}])

    client = make_client(handler)
    client.list_bots()
    client.list_bots()
    assert calls["oauth"] == 1  # токен взят один раз и переиспользован
    assert calls["api"] == 2


def test_refreshes_token_after_expiry():
    calls = {"oauth": 0}
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["oauth"] += 1
            return _token_response(expires_in=3600, token=f"tok-{calls['oauth']}")
        return httpx.Response(200, json=[])

    client = make_client(handler, clock=clock)
    client.list_bots()
    clock.advance(3601)  # токен протух
    client.list_bots()
    assert calls["oauth"] == 2  # запросили новый токен


def test_iter_subscribers_follows_cursor_across_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response()
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(200, json={
                "data": [{"id": 2}, {"id": 3}],
                "paging": {"cursor": {"after": 3}, "next": "after=3"},
            })
        if after == "3":
            return httpx.Response(200, json={
                "data": [{"id": 4}],
                "paging": None,  # конец
            })
        raise AssertionError(f"unexpected after={after}")

    client = make_client(handler)
    ids = [s["id"] for s in client.iter_subscribers()]
    assert ids == [2, 3, 4]


def test_iter_subscribers_single_page_when_no_paging():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response()
        return httpx.Response(200, json={"data": [{"id": 1}], "paging": None})

    client = make_client(handler)
    assert [s["id"] for s in client.iter_subscribers()] == [1]


def test_list_steps_returns_bare_list():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response()
        assert request.url.path == "/v1/bots/REF/steps"
        return httpx.Response(200, json=[{"title": "Приветствие", "referral": "s1"}])

    client = make_client(handler)
    steps = client.list_steps("REF")
    assert steps == [{"title": "Приветствие", "referral": "s1"}]


def test_retries_once_on_401_with_fresh_token():
    state = {"oauth": 0, "served401": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            state["oauth"] += 1
            return _token_response(token=f"tok-{state['oauth']}")
        if not state["served401"]:
            state["served401"] = True
            return httpx.Response(401, json={"error": "expired"})
        assert request.headers["Authorization"] == "Bearer tok-2"
        return httpx.Response(200, json=[{"ok": True}])

    client = make_client(handler)
    assert client.list_bots() == [{"ok": True}]
    assert state["oauth"] == 2  # протухший токен -> один повтор со свежим
