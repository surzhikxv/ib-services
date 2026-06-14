"""Чистая логика маппинга BotHelp → единая модель воронки.

Никаких сетевых вызовов и БД — только преобразования. Это ядро «распознавания
тарифов и оплат» и «единой событийной модели воронки» из брифа, и оно покрыто
тестами на реальных данных (tests/test_mapping.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Tariff(str, Enum):
    PREMIUM = "premium"
    BASIC = "basic"
    STANDARD = "standard"


class StageKey(str, Enum):
    """Канонические этапы воронки (наша модель, BotHelp аналитику не отдаёт)."""

    WELCOME = "welcome"
    PACKAGE_CHOICE = "package_choice"
    PACKAGE_INFO = "package_info"
    CHECKOUT = "checkout"
    PAID = "paid"
    CHURN = "churn"
    SERVICE = "service"
    UNKNOWN = "unknown"


class StepRole(str, Enum):
    ENTRY = "entry"
    CHOICE = "choice"
    INFO = "info"
    CHECKOUT = "checkout"
    ACCESS_REMOVAL = "access_removal"
    MESSAGE = "message"
    DELAY = "delay"
    ACTION = "action"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StepClassification:
    stage: StageKey
    tariff: Tariff | None
    role: StepRole


@dataclass(frozen=True)
class DerivedEvent:
    """Событие единой модели воронки, выведенное из данных подписчика."""

    event_type: str  # 'bot_start' | 'payment'
    occurred_at: int  # unix-секунды
    tariff: Tariff | None = None
    source_tag: str | None = None


@dataclass(frozen=True)
class SourceInfo:
    platform: str | None
    channel_name: str | None
    cuid: str | None
    utm: dict


# Порядок важен: «базовый/база» проверяем по подстроке «баз».
_TARIFF_KEYWORDS = (
    (Tariff.PREMIUM, "премиум"),
    (Tariff.STANDARD, "стандарт"),
    (Tariff.BASIC, "баз"),
)

# Префиксы покупки в тегах BotHelp (теги вида «купил_базовый», «купил_премиум_»).
# Именно префикс, а не подстрока: иначе «..._купил» в произвольном теге дал бы ложную оплату.
_PURCHASE_PREFIXES = ("куп", "оплат", "опла")

_UTM_FIELDS = ("utmSource", "utmMedium", "utmCampaign", "utmContent", "utmTerm")


def tariff_from_text(text: str | None) -> Tariff | None:
    """Находит тариф по ключевому слову в произвольном тексте (регистронезависимо)."""
    if not text:
        return None
    low = text.lower()
    for tariff, kw in _TARIFF_KEYWORDS:
        if kw in low:
            return tariff
    return None


def classify_step(title: str | None) -> StepClassification:
    """Классифицирует шаг бота в этап воронки, тариф и роль шага."""
    raw = title or ""
    low = raw.lower().strip()

    if not low:
        return StepClassification(StageKey.UNKNOWN, None, StepRole.UNKNOWN)

    if "приветств" in low:
        return StepClassification(StageKey.WELCOME, None, StepRole.ENTRY)
    if "выбор пакета" in low:
        return StepClassification(StageKey.PACKAGE_CHOICE, None, StepRole.CHOICE)
    if "инфо о пакет" in low:
        return StepClassification(StageKey.PACKAGE_INFO, None, StepRole.INFO)
    if low.startswith("оплат"):
        return StepClassification(StageKey.CHECKOUT, tariff_from_text(low), StepRole.CHECKOUT)
    if low.startswith("удален"):
        return StepClassification(StageKey.CHURN, tariff_from_text(low), StepRole.ACCESS_REMOVAL)
    if low.startswith("сообщен"):
        return StepClassification(StageKey.SERVICE, None, StepRole.MESSAGE)
    if low.startswith("задержк"):
        return StepClassification(StageKey.SERVICE, None, StepRole.DELAY)
    if low.startswith("действи"):
        return StepClassification(StageKey.SERVICE, None, StepRole.ACTION)

    return StepClassification(StageKey.UNKNOWN, None, StepRole.UNKNOWN)


def parse_payment_tag(tag: str | None) -> Tariff | None:
    """Тариф из тега-оплаты, либо None если тег не про покупку.

    BotHelp кодирует оплаты тегами «купил_<тариф>» (напр. «купил_премиум_»).
    """
    if not tag:
        return None
    low = tag.lower()
    if not low.startswith(_PURCHASE_PREFIXES):
        return None
    return tariff_from_text(low)


def derive_subscriber_events(sub: dict) -> list[DerivedEvent]:
    """Выводит события воронки из записи подписчика.

    - bot_start — момент попадания в бота (createdAt);
    - payment — по каждому тегу-покупке (точное время оплаты придёт вебхуком,
      пока берём createdAt как нижнюю оценку).
    """
    created = int(sub.get("createdAt") or 0)
    events: list[DerivedEvent] = [DerivedEvent("bot_start", created)]
    for tag in sub.get("tags") or []:
        tariff = parse_payment_tag(tag)
        if tariff is not None:
            events.append(DerivedEvent("payment", created, tariff=tariff, source_tag=tag))
    return events


def source_from_subscriber(sub: dict) -> SourceInfo:
    """Извлекает атрибуцию трафика: площадка, имя канала, cuid, непустые UTM."""
    utm = {k: sub[k] for k in _UTM_FIELDS if sub.get(k)}
    return SourceInfo(
        platform=sub.get("channelType") or None,
        channel_name=sub.get("channelName") or None,
        cuid=sub.get("cuid") or None,
        utm=utm,
    )
