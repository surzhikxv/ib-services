from datetime import datetime, timezone

from kontur.db import make_engine, make_session_factory, upsert
from kontur.models import Base, OAuthToken


def _session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)()


def test_oauth_token_upsert_by_connector():
    s = _session()
    exp = datetime(2026, 8, 1, tzinfo=timezone.utc)
    _, created1 = upsert(s, OAuthToken, {"connector": "youtube"},
                         {"access_token": "a1", "refresh_token": "r1", "expires_at": exp})
    s.flush()
    _, created2 = upsert(s, OAuthToken, {"connector": "youtube"},
                         {"access_token": "a2", "refresh_token": "r1", "expires_at": exp})
    s.flush()
    tok = s.query(OAuthToken).filter_by(connector="youtube").one()
    assert created1 is True and created2 is False
    assert tok.access_token == "a2"  # refreshed in place
