"""TDD: оркестрация выгрузки BotHelp → озеро данных.

Клиент подменён (FakeClient) данными в форме живого API; БД — in-memory SQLite.
Проверяем: корректные сущности/события/оплаты, атрибуцию по UTM и идемпотентность.
"""
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from kontur.db import init_db, make_session_factory
from kontur.models import Event, FunnelStep, Payment, Source, Subscriber
from kontur.connectors.bothelp.sync import sync_bothelp


class FakeClient:
    """Повторяет форму ответов живого BotHelp API."""

    def list_bots(self):
        return [{"title": "Курс", "referral": "REF"}]

    def list_steps(self, bot_referral):
        assert bot_referral == "REF"
        return [
            {"title": "Приветствие", "referral": "s1"},
            {"title": "Оплата премиум", "referral": "s2"},
            {"title": "удаление база", "referral": "s3"},
            {"title": "Действия 1", "referral": "s4"},
        ]

    def iter_subscribers(self):
        base = {"channelType": "telegram", "channelName": "Канал",
                "utmSource": "", "utmCampaign": "", "utmMedium": "",
                "utmContent": "", "utmTerm": ""}
        return iter([
            {**base, "id": 2, "createdAt": 1778267935, "cuid": "dc2s.2", "tags": []},
            {**base, "id": 3, "createdAt": 1778269560, "cuid": "dc2s.3",
             "tags": ["купил_базовый", "купил_стандарт"]},
            {**base, "id": 4, "createdAt": 1778271009, "cuid": "dc2s.4",
             "tags": ["купил_премиум_"]},
            {**base, "id": 5, "createdAt": 1778272000, "cuid": "dc2s.5", "tags": [],
             "utmSource": "tiktok", "utmCampaign": "spring"},
        ])


def _memory_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    return engine, make_session_factory(engine)


def _counts(session):
    return {
        "subscribers": session.scalar(select(func.count()).select_from(Subscriber)),
        "events": session.scalar(select(func.count()).select_from(Event)),
        "payments": session.scalar(select(func.count()).select_from(Payment)),
        "steps": session.scalar(select(func.count()).select_from(FunnelStep)),
        "sources": session.scalar(select(func.count()).select_from(Source)),
    }


def test_sync_writes_expected_entities():
    engine, factory = _memory_factory()
    stats = sync_bothelp(FakeClient(), factory, bot_referral="REF")

    with factory() as s:
        c = _counts(s)
    assert c["subscribers"] == 4
    assert c["steps"] == 4
    # 4 bot_start + (2+1+0) payment-событий = 7
    assert c["events"] == 7
    assert c["payments"] == 3
    assert c["sources"] == 1  # только подписчик с utmSource
    assert stats["subscribers"] == 4 and stats["payments"] == 3


def test_payment_events_carry_correct_tariff():
    engine, factory = _memory_factory()
    sync_bothelp(FakeClient(), factory, bot_referral="REF")
    with factory() as s:
        tariffs = s.scalars(
            select(Payment).join(Payment.tariff)
        ).all()
        keys = sorted(p.tariff.key for p in tariffs)
    assert keys == ["basic", "premium", "standard"]


def test_utm_subscriber_is_linked_to_source():
    engine, factory = _memory_factory()
    sync_bothelp(FakeClient(), factory, bot_referral="REF")
    with factory() as s:
        sub = s.scalar(select(Subscriber).where(Subscriber.external_id == "5"))
        assert sub.source_id is not None
        src = s.get(Source, sub.source_id)
        assert src.utm_source == "tiktok"


def test_sync_is_idempotent():
    engine, factory = _memory_factory()
    sync_bothelp(FakeClient(), factory, bot_referral="REF")
    with factory() as s:
        first = _counts(s)
    sync_bothelp(FakeClient(), factory, bot_referral="REF")  # повтор
    with factory() as s:
        second = _counts(s)
    assert first == second  # повторный прогон не плодит дублей
