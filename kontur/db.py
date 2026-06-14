"""Подключение к БД, инициализация схемы, сиды справочников и портируемый upsert."""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from kontur.config import get_settings
from kontur.models import Base, FunnelStage, Tariff

# Канонические тарифы и этапы воронки — сиды, на которые опирается маппинг.
SEED_TARIFFS = [
    {"key": "premium", "title": "Премиум"},
    {"key": "basic", "title": "Базовый"},
    {"key": "standard", "title": "Стандарт"},
]

SEED_STAGES = [
    {"key": "welcome", "title": "Приветствие", "ordering": 10, "stage_type": "entry"},
    {"key": "package_choice", "title": "Выбор пакета", "ordering": 20, "stage_type": "choice"},
    {"key": "package_info", "title": "Инфо о пакетах", "ordering": 30, "stage_type": "info"},
    {"key": "checkout", "title": "Оплата", "ordering": 40, "stage_type": "checkout"},
    {"key": "paid", "title": "Оплачено", "ordering": 50, "stage_type": "paid"},
    {"key": "churn", "title": "Отписка/снятие доступа", "ordering": 60, "stage_type": "churn"},
    {"key": "service", "title": "Служебные шаги", "ordering": 90, "stage_type": "service"},
    {"key": "unknown", "title": "Не определён", "ordering": 99, "stage_type": "service"},
]


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    # future-safe defaults; pre_ping не мешает sqlite и спасает долгие postgres-сессии
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def upsert(session: Session, model, natural_key: dict[str, Any], values: dict[str, Any]):
    """Портируемый upsert по естественному ключу (works на Postgres и SQLite).

    Возвращает (объект, created: bool). Для малых объёмов коннектора достаточно
    select-then-write; не завязываемся на диалект-специфичный ON CONFLICT.
    """
    stmt = select(model).filter_by(**natural_key)
    obj = session.execute(stmt).scalar_one_or_none()
    if obj is None:
        obj = model(**natural_key, **values)
        session.add(obj)
        return obj, True
    for k, v in values.items():
        setattr(obj, k, v)
    return obj, False


def seed_reference_data(session: Session) -> None:
    for t in SEED_TARIFFS:
        upsert(session, Tariff, {"key": t["key"]}, {"title": t["title"]})
    for s in SEED_STAGES:
        upsert(session, FunnelStage, {"key": s["key"]},
               {"title": s["title"], "ordering": s["ordering"], "stage_type": s["stage_type"]})


def init_db(engine: Engine) -> None:
    """Создаёт таблицы, заливает сиды справочников и вьюхи дашборда (идемпотентно)."""
    from kontur.dashboard.views import create_views

    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as session:
        seed_reference_data(session)
        session.commit()
    create_views(engine)
