from datetime import datetime, timezone

from kontur.connectors.oauth import load_token, save_token
from kontur.db import make_engine, make_session_factory
from kontur.models import Base


def _factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_load_token_roundtrip():
    factory = _factory()
    assert load_token(factory, "instagram") is None
    exp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    save_token(factory, "instagram", access_token="tok", expires_at=exp)
    row = load_token(factory, "instagram")
    assert row.access_token == "tok" and row.expires_at == exp
