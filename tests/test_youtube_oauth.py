import httpx

from kontur.connectors.youtube.client import exchange_refresh_token
from tests.youtube_fake import make_transport


def test_exchange_refresh_token_posts_form_and_parses():
    transport, calls = make_transport(token={"access_token": "fresh", "expires_in": 3599})
    out = exchange_refresh_token("rtok", "cid", "secret", transport=transport)
    assert out["access_token"] == "fresh"
    assert out["expires_in"] == 3599
    seg, params, _ = calls[0]
    assert seg == "token"


def test_proxy_and_transport_mutually_exclusive():
    # прод несёт proxy_url (без transport) — построение клиента не падает
    try:
        exchange_refresh_token("r", "c", "s", proxy_url="http://relay:3128",
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        assert False, "expected ValueError"
    except ValueError:
        pass
