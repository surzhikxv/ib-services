from kontur.connectors.instagram.client import InstagramClient, InstagramError
from tests.instagram_fake import make_transport

ME = {"user_id": "17841400000000000", "username": "lapychev", "account_type": "Media_Creator",
      "followers_count": 1200, "follows_count": 80, "media_count": 3}


def _client(transport):
    return InstagramClient("tok", transport=transport, sleep=lambda *_: None)


def test_me_returns_profile():
    transport, calls = make_transport(me=ME, media_pages=[[]])
    with _client(transport) as c:
        assert c.me()["username"] == "lapychev"
    assert calls[0][0] == "me"


def test_iter_media_follows_cursor_pages():
    pages = [[{"id": "1"}, {"id": "2"}], [{"id": "3"}]]
    transport, _ = make_transport(me=ME, media_pages=pages)
    with _client(transport) as c:
        ids = [m["id"] for m in c.iter_media()]
    assert ids == ["1", "2", "3"]


def test_page_instagram_account_resolves_linked_account():
    page_account = {"id": "17841400000000000", "username": "lapychev"}
    transport, calls = make_transport(me=ME, media_pages=[[]], page_account=page_account)
    with _client(transport) as c:
        account = c.page_instagram_account("fb-page-1")
    assert account["id"] == "17841400000000000"
    assert calls[0][0] == "fb-page-1"


def test_iter_media_accepts_explicit_account_id():
    pages = [[{"id": "1"}]]
    transport, calls = make_transport(me=ME, media_pages=pages)
    with _client(transport) as c:
        ids = [m["id"] for m in c.iter_media("17841400000000000")]
    assert ids == ["1"]
    assert calls[0][0] == "media"


def test_error_body_raises_instagram_error():
    transport, _ = make_transport(me=ME, media_pages=[[]],
                                  errors={"me": {"code": 190, "message": "bad token"}})
    with _client(transport) as c:
        try:
            c.me()
            assert False, "expected InstagramError"
        except InstagramError as e:
            assert e.code == 190
