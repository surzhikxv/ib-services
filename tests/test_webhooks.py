"""TDD: приём живых событий вебхуком (каркас).

Бриф: живые события (оплата, тег, проход шага) лучше принимать вебхуком, а не
опросом API. Здесь — чистый приёмник: складывает сырой payload в озеро (idempotent).
"""
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from kontur.db import init_db, make_session_factory
from kontur.models import RawRecord
from kontur.webhooks import record_webhook, webhook_authorized


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    return make_session_factory(engine)


def test_webhook_stored_as_raw_record():
    factory = _factory()
    record_webhook(factory, "crm", {"id": "evt-1", "type": "payment"})
    with factory() as s:
        row = s.scalar(select(RawRecord).where(RawRecord.entity_type == "webhook"))
        assert row.source_system == "crm"
        assert row.external_id == "evt-1"
        assert row.payload["type"] == "payment"


def test_same_event_id_is_idempotent():
    factory = _factory()
    record_webhook(factory, "crm", {"id": "evt-1", "type": "payment"})
    record_webhook(factory, "crm", {"id": "evt-1", "type": "payment"})
    with factory() as s:
        n = s.scalar(select(func.count()).select_from(RawRecord))
    assert n == 1


def test_payload_without_id_falls_back_to_content_hash():
    factory = _factory()
    record_webhook(factory, "prodamus", {"order": 100, "sum": "5000"})
    record_webhook(factory, "prodamus", {"order": 101, "sum": "3000"})
    with factory() as s:
        n = s.scalar(select(func.count()).select_from(RawRecord))
    assert n == 2  # разные payload -> разные записи


def test_generic_webhook_is_disabled_without_server_token():
    assert not webhook_authorized(
        "crm",
        "request-token",
        expected_token="",
        allowed_sources="crm",
    )


def test_generic_webhook_requires_matching_token_and_allowed_source():
    assert webhook_authorized(
        "crm",
        "request-token",
        expected_token="request-token",
        allowed_sources="crm, marketing",
    )
    assert not webhook_authorized(
        "crm",
        "wrong-token",
        expected_token="request-token",
        allowed_sources="crm, marketing",
    )
    assert not webhook_authorized(
        "prodamus",
        "request-token",
        expected_token="request-token",
        allowed_sources="crm, marketing",
    )
