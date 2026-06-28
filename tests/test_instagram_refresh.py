from kontur.connectors.instagram.client import InstagramClient
from tests.instagram_fake import make_transport


def test_refresh_token_returns_new_token():
    transport, calls = make_transport(me={}, media_pages=[[]])
    c = InstagramClient("old-token", transport=transport, sleep=lambda *_: None)
    out = c.refresh_token()
    assert out["access_token"] == "refreshed-token"
    assert out["expires_in"] == 5184000
    seg, params = next((s, p) for s, p in calls if s == "refresh_access_token")
    assert params["grant_type"] == "ig_refresh_token"
