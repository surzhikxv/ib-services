from datetime import datetime, timedelta, timezone

import pytest

from kontur.connectors.instagram.client import InstagramClient
from kontur.connectors.instagram.sync import refresh_if_stale, resolve_token, token_store_key
from kontur.connectors.oauth import load_token, save_token
from kontur.db import make_engine, make_session_factory
from kontur.models import Base
from tests.instagram_fake import make_transport

NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _client_factory(token):
    transport, _ = make_transport(me={}, media_pages=[[]])
    return InstagramClient(token, transport=transport, sleep=lambda *_: None)


def test_resolve_token_bootstraps_from_env():
    factory = _factory()
    assert resolve_token(factory, env_token="env-tok") == "env-tok"
    assert load_token(factory, "instagram").access_token == "env-tok"   # persisted


def test_resolve_token_prefers_store():
    factory = _factory()
    save_token(factory, "instagram", access_token="stored")
    assert resolve_token(factory, env_token="env-tok") == "stored"


def test_facebook_mode_uses_separate_token_store_key():
    factory = _factory()
    save_token(factory, "instagram", access_token="instagram-token")
    assert token_store_key("facebook") == "instagram_facebook"
    assert resolve_token(factory, env_token="facebook-token",
                         connector=token_store_key("facebook")) == "facebook-token"
    assert load_token(factory, "instagram").access_token == "instagram-token"
    assert load_token(factory, "instagram_facebook").access_token == "facebook-token"


def test_resolve_token_raises_without_any():
    with pytest.raises(RuntimeError):
        resolve_token(_factory(), env_token="")


def test_refresh_if_stale_refreshes_near_expiry():
    factory = _factory()
    save_token(factory, "instagram", access_token="old",
               expires_at=NOW + timedelta(days=3))      # within 7-day threshold
    out = refresh_if_stale(factory, _client_factory, now=NOW)
    assert out["refreshed"] is True
    assert load_token(factory, "instagram").access_token == "refreshed-token"
    assert out["expires_at"] == NOW + timedelta(seconds=5184000)


def test_refresh_if_stale_skips_when_fresh():
    factory = _factory()
    save_token(factory, "instagram", access_token="old",
               expires_at=NOW + timedelta(days=40))     # far from expiry
    out = refresh_if_stale(factory, _client_factory, now=NOW)
    assert out["refreshed"] is False
    assert load_token(factory, "instagram").access_token == "old"


def test_refresh_if_stale_tolerates_api_error():
    # bootstrap token: expires_at=None → treated as stale, but a <24h-old token
    # cannot be refreshed (Meta error). Must NOT break the run; keep current token.
    from kontur.connectors.instagram.client import InstagramError

    def _erroring_factory(token):
        class _C:
            def refresh_token(self):
                raise InstagramError(2, "token too young to refresh")
            def close(self):
                pass
        return _C()

    factory = _factory()
    save_token(factory, "instagram", access_token="fresh", expires_at=None)
    out = refresh_if_stale(factory, _erroring_factory, now=NOW)
    assert out["refreshed"] is False
    assert load_token(factory, "instagram").access_token == "fresh"   # unchanged, run survives
