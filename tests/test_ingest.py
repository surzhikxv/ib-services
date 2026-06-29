from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from kontur.db import init_db, make_session_factory
from kontur.models import Event, Subscriber
from kontur import ingest


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    return make_session_factory(engine)


def test_bot_start_creates_subscriber_and_event_idempotently():
    sf = _factory()
    ingest.record_bot_start(101, session_factory=sf)
    ingest.record_bot_start(101, session_factory=sf)  # repeat → no dup
    s = sf()
    subs = s.scalars(select(Subscriber).where(Subscriber.source_system == "telegram_bot")).all()
    evs = s.scalars(select(Event).where(Event.source_system == "telegram_bot")).all()
    assert len(subs) == 1 and subs[0].external_id == "101" and subs[0].tg_user_id == "101"
    assert len(evs) == 1
    e = evs[0]
    assert e.event_type == "bot_start" and e.dedup_key == "tg101:bot_start"
    assert e.subscriber_id == subs[0].id and e.funnel_stage_id is not None  # 'welcome' resolved


def test_payment_event_carries_tariff_amount_and_paid_stage():
    sf = _factory()
    ingest.record_payment(202, "premium", "tg202-premium-1700000000",
                          amount=2990.0, currency="rub", raw={"x": 1}, session_factory=sf)
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "payment")).one()
    assert e.dedup_key == "tg202:payment:tg202-premium-1700000000"
    assert float(e.amount) == 2990.0 and e.currency == "rub"
    assert e.tariff_id is not None and e.funnel_stage_id is not None  # 'premium' + 'paid' resolved
    assert e.raw == {"x": 1}


def test_step_enter_dedup_key_and_idempotency():
    sf = _factory()
    ingest.record_step_enter(303, 3, stage_key="package_info", tariff_key="standard", session_factory=sf)
    ingest.record_step_enter(303, 3, stage_key="package_info", tariff_key="standard", session_factory=sf)  # re-enter → no dup
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "step_enter")).one()
    assert e.dedup_key == "tg303:step:3"
    assert e.tariff_id is not None and e.funnel_stage_id is not None  # 'standard' + 'package_info' resolved
    assert s.scalar(select(func.count()).select_from(Event).where(Event.event_type == "step_enter")) == 1


def test_step_enter_uid_makes_events_append_only():
    sf = _factory()
    ingest.record_step_enter(606, 3, uid="cqA", stage_key="package_info",
                             tariff_key="standard", session_factory=sf)
    ingest.record_step_enter(606, 3, uid="cqB", stage_key="package_info",
                             tariff_key="standard", session_factory=sf)
    ingest.record_step_enter(606, 3, uid="cqA", stage_key="package_info",
                             tariff_key="standard", session_factory=sf)  # тот же uid → без дубля
    s = sf()
    evs = s.scalars(select(Event).where(Event.event_type == "step_enter")).all()
    assert {e.dedup_key for e in evs} == {"tg606:step:3:cqA", "tg606:step:3:cqB"}
    assert len(evs) == 2  # два разных uid → две строки; повтор uid идемпотентен


def test_bot_start_uid_key():
    sf = _factory()
    ingest.record_bot_start(707, uid="m5", session_factory=sf)
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "bot_start")).one()
    assert e.dedup_key == "tg707:start:m5" and e.funnel_stage_id is not None


def test_record_applied_event():
    sf = _factory()
    ingest.record_applied(808, 5, "Подал заявку", uid="cqZ", session_factory=sf)
    s = sf()
    e = s.scalars(select(Event).where(Event.event_type == "applied")).one()
    assert e.dedup_key == "tg808:applied:cqZ"
    assert e.funnel_stage_id is not None  # 'paid' resolved
    assert e.raw == {"button": "Подал заявку", "step": 5}
