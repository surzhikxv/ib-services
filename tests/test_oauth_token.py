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


def test_save_token_persists_in_independent_session():
    from kontur.connectors.oauth import save_token
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    save_token(factory, "instagram", access_token="A", refresh_token="R",
               expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    # a brand-new session sees it → it was really committed, not left pending
    s2 = factory()
    tok = s2.query(OAuthToken).filter_by(connector="instagram").one()
    assert tok.access_token == "A" and tok.refresh_token == "R"
    # second call updates in place (idempotent by connector)
    save_token(factory, "instagram", access_token="A2", refresh_token="R2")
    s3 = factory()
    rows = s3.query(OAuthToken).filter_by(connector="instagram").all()
    assert len(rows) == 1 and rows[0].access_token == "A2"
