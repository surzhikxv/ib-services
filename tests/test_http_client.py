import httpx
import pytest

from kontur.connectors.http import build_http_client


def test_transport_is_used_when_given():
    mock = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    client = build_http_client(transport=mock)
    assert client._transport is mock
    r = client.get("https://example.test/ping")
    assert r.json() == {"ok": True}


def test_proxy_builds_http_transport_not_mock():
    client = build_http_client(proxy_url="http://user:pass@127.0.0.1:3128")
    # prod path must carry a real proxied transport, never silently dropped
    assert isinstance(client._transport, httpx.HTTPTransport)


def test_both_proxy_and_transport_raises():
    mock = httpx.MockTransport(lambda req: httpx.Response(200))
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_http_client(proxy_url="http://127.0.0.1:3128", transport=mock)


def test_neither_returns_plain_client():
    client = build_http_client()
    assert isinstance(client, httpx.Client)
