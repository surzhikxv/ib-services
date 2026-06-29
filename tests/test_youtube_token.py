from datetime import datetime, timedelta, timezone

from kontur.connectors.oauth import load_token, save_token
from kontur.connectors.youtube.sync import ensure_access_token, resolve_refresh_token
from kontur.db import make_engine, make_session_factory
from kontur.models import Base

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_resolve_refresh_token_bootstraps_from_env():
    f = _factory()
    assert resolve_refresh_token(f, env_refresh="rtok") == "rtok"
    assert load_token(f, "youtube").refresh_token == "rtok"   # сохранён


def test_ensure_access_token_refreshes_when_missing():
    f = _factory()
    save_token(f, "youtube", refresh_token="rtok")            # access ещё нет
    calls = []

    def fake_exchange(refresh, cid, secret, **kw):
        calls.append((refresh, cid, secret))
        return {"access_token": "fresh", "expires_in": 3600}

    tok = ensure_access_token(f, client_id="cid", client_secret="sec", now=NOW,
                              exchange=fake_exchange)
    assert tok == "fresh"
    assert calls == [("rtok", "cid", "sec")]
    row = load_token(f, "youtube")
    assert row.access_token == "fresh"
    assert row.refresh_token == "rtok"                        # refresh не потерян
    assert row.expires_at > NOW


def test_ensure_access_token_reuses_valid():
    f = _factory()
    save_token(f, "youtube", refresh_token="rtok", access_token="still-good",
               expires_at=NOW + timedelta(hours=1))

    def boom(*a, **k):
        raise AssertionError("refresh не должен вызываться, токен ещё валиден")

    assert ensure_access_token(f, client_id="c", client_secret="s", now=NOW,
                               exchange=boom) == "still-good"
