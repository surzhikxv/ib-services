"""Схема озера данных «Контур роста».

Единая модель для всех каналов: сырое озеро (raw_records) + нормализованные
сущности (каналы, контент, подписчики, источники, тарифы, шаги и этапы воронки,
оплаты) + единая событийная модель воронки (events).

Источник истины для схемы — этот файл. Боевая БД — PostgreSQL (docker-compose),
но типы портируемы: JSON→JSONB только на Postgres, что позволяет гонять весь
конвейер на SQLite для локальной проверки на живых данных.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB на Postgres, обычный JSON на остальных диалектах (SQLite).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Сырое озеро ----------------------------------------------------------

class RawRecord(Base, TimestampMixin):
    """Сырой payload из любого коннектора до нормализации (data lake landing)."""

    __tablename__ = "raw_records"
    __table_args__ = (UniqueConstraint("source_system", "entity_type", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(50))  # bothelp, youtube, ...
    entity_type: Mapped[str] = mapped_column(String(50))     # bot, step, subscriber, webhook
    external_id: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSONType)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Каналы / источники / контент ----------------------------------------

class Channel(Base, TimestampMixin):
    """Площадка/канал: TikTok, YouTube, Instagram, VK, Telegram, BotHelp."""

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict | None] = mapped_column(JSONType)


class Source(Base, TimestampMixin):
    """Источник трафика: UTM-метка / старт-ссылка / реферал."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("kind", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"))
    kind: Mapped[str] = mapped_column(String(50))  # utm, start_link, referral
    code: Mapped[str] = mapped_column(String(500))
    utm_source: Mapped[str | None] = mapped_column(String(255))
    utm_medium: Mapped[str | None] = mapped_column(String(255))
    utm_campaign: Mapped[str | None] = mapped_column(String(255))
    utm_content: Mapped[str | None] = mapped_column(String(255))
    utm_term: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[dict | None] = mapped_column(JSONType)


class Content(Base, TimestampMixin):
    """Единица контента: видео/пост/шортс/эфир."""

    __tablename__ = "content"
    __table_args__ = (UniqueConstraint("channel_id", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    external_id: Mapped[str] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict | None] = mapped_column(JSONType)  # ПОСЛЕДНИЙ/накопительный снимок — дешёвое чтение; история — в content_metrics
    raw: Mapped[dict | None] = mapped_column(JSONType)
    last_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"))


class ContentMetric(Base, TimestampMixin):
    """Снимок метрик контента за один UTC-день (тайм-серия/история).

    Одна НЕИЗМЕНЯЕМАЯ строка на контент/UTC-день. Коннекторы пишут ОБА хранилища:
    - ``content.metrics`` — ПОСЛЕДНИЙ/накопительный снимок (дешёвое чтение);
    - ``content_metrics`` (эта таблица) — ежедневная история (тайм-серия).
    ``snapshot_date`` — UTC-календарная дата (не datetime).
    """

    __tablename__ = "content_metrics"
    __table_args__ = (UniqueConstraint("content_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    views: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSONType)


# --- Тарифы и воронка -----------------------------------------------------

class Tariff(Base, TimestampMixin):
    """Тариф/продукт: премиум / базовый / стандарт."""

    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)  # premium/basic/standard
    title: Mapped[str] = mapped_column(String(255))
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8), default="RUB")
    meta: Mapped[dict | None] = mapped_column(JSONType)


class FunnelStage(Base, TimestampMixin):
    """Канонический этап воронки (наша модель — BotHelp аналитику не отдаёт)."""

    __tablename__ = "funnel_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    ordering: Mapped[int] = mapped_column(Integer, default=0)
    stage_type: Mapped[str | None] = mapped_column(String(50))


class FunnelStep(Base, TimestampMixin):
    """Шаг бота BotHelp, замапленный на канонический этап и тариф."""

    __tablename__ = "funnel_steps"
    __table_args__ = (UniqueConstraint("bot_referral", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"))
    bot_referral: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(255))  # referral шага
    title: Mapped[str] = mapped_column(String(500))
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("funnel_stages.id"))
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))
    role: Mapped[str | None] = mapped_column(String(50))
    ordering: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict | None] = mapped_column(JSONType)


# --- Подписчики / события / оплаты ----------------------------------------

class Subscriber(Base, TimestampMixin):
    """Человек: подписчик бота (единая запись)."""

    __tablename__ = "subscribers"
    __table_args__ = (UniqueConstraint("source_system", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(255))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    tg_user_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    cuid: Mapped[str | None] = mapped_column(String(128))
    prodamus_profile_id: Mapped[str | None] = mapped_column(String(128))
    subscribed: Mapped[bool] = mapped_column(Boolean, default=True)
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[list | None] = mapped_column(JSONType)
    raw: Mapped[dict | None] = mapped_column(JSONType)

    events: Mapped[list["Event"]] = relationship(back_populates="subscriber")
    payments: Mapped[list["Payment"]] = relationship(back_populates="subscriber")


class Event(Base):
    """Единая событийная модель воронки. Идемпотентность — по (source_system, dedup_key)."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source_system", "dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscriber_id: Mapped[int | None] = mapped_column(ForeignKey("subscribers.id"))
    event_type: Mapped[str] = mapped_column(String(50))  # bot_start, payment, step_enter, ...
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    content_id: Mapped[int | None] = mapped_column(ForeignKey("content.id"))
    funnel_stage_id: Mapped[int | None] = mapped_column(ForeignKey("funnel_stages.id"))
    funnel_step_id: Mapped[int | None] = mapped_column(ForeignKey("funnel_steps.id"))
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    source_system: Mapped[str] = mapped_column(String(50))
    dedup_key: Mapped[str] = mapped_column(String(500))
    raw: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriber: Mapped["Subscriber"] = relationship(back_populates="events")


class Payment(Base, TimestampMixin):
    """Оплата (Prodamus внутри BotHelp; провайдер фиксируем явно)."""

    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscriber_id: Mapped[int | None] = mapped_column(ForeignKey("subscribers.id"))
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32), default="succeeded")
    provider: Mapped[str] = mapped_column(String(50))  # prodamus, bothelp
    external_id: Mapped[str] = mapped_column(String(255))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    raw: Mapped[dict | None] = mapped_column(JSONType)

    subscriber: Mapped["Subscriber"] = relationship(back_populates="payments")
    tariff: Mapped["Tariff | None"] = relationship()


# --- Журнал запусков коннекторов ------------------------------------------

class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running")  # running/ok/error
    stats: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)


class OAuthToken(Base, TimestampMixin):
    """Хранилище OAuth-токенов коннекторов (refresh должен переживать рестарт процесса).

    ВАЖНО: запись токена должна выполняться в ОТДЕЛЬНОЙ сессии с немедленным commit —
    НЕ внутри транзакции ingest. Используй ``kontur.connectors.oauth.save_token``.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String(50), unique=True)
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONType)


# --- Разборы ИИ-аналитика -------------------------------------------------

class AiReport(Base):
    """Разбор/ответ ИИ-аналитика: текст для владельца + срез данных, по которому он сделан."""

    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # weekly | adhoc
    period: Mapped[str | None] = mapped_column(String(64))
    question: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    digest: Mapped[dict | None] = mapped_column(JSONType)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
