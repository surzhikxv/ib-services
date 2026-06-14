"""TDD: чистая логика маппинга BotHelp → единая модель воронки.

Входные данные — РЕАЛЬНЫЕ (сняты с живого API клиента 2026-06-15):
  шаги бота «Курс» (28 шт.) и теги-оплаты (купил_базовый / купил_стандарт / купил_премиум_).
"""
import pytest

from kontur.connectors.bothelp.mapping import (
    StageKey,
    StepRole,
    Tariff,
    classify_step,
    derive_subscriber_events,
    parse_payment_tag,
    source_from_subscriber,
    tariff_from_text,
)


# --- tariff_from_text: распознавание тарифа в произвольном тексте ---------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("премиум", Tariff.PREMIUM),
        ("Оплата премиум", Tariff.PREMIUM),
        ("удаление премиум", Tariff.PREMIUM),
        ("купил_премиум_", Tariff.PREMIUM),  # хвостовое подчёркивание из живых данных
        ("базовый", Tariff.BASIC),
        ("Оплата базовый", Tariff.BASIC),
        ("удаление база", Tariff.BASIC),  # «база», не «базовый»
        ("стандарт", Tariff.STANDARD),
        ("Оплата стандарт", Tariff.STANDARD),
        ("удаление стандарт", Tariff.STANDARD),
        ("ПРЕМИУМ", Tariff.PREMIUM),  # регистронезависимо
        ("Приветствие", None),
        ("", None),
    ],
)
def test_tariff_from_text(text, expected):
    assert tariff_from_text(text) == expected


# --- classify_step: 28 реальных шагов бота «Курс» -------------------------

@pytest.mark.parametrize(
    "title, stage, tariff, role",
    [
        ("Приветствие", StageKey.WELCOME, None, StepRole.ENTRY),
        ("Выбор пакета", StageKey.PACKAGE_CHOICE, None, StepRole.CHOICE),
        ("Инфо о пакетах 1", StageKey.PACKAGE_INFO, None, StepRole.INFO),
        ("Инфо о пакетах 2", StageKey.PACKAGE_INFO, None, StepRole.INFO),
        ("Инфо о пакетах 3", StageKey.PACKAGE_INFO, None, StepRole.INFO),
        ("Оплата премиум", StageKey.CHECKOUT, Tariff.PREMIUM, StepRole.CHECKOUT),
        ("Оплата базовый", StageKey.CHECKOUT, Tariff.BASIC, StepRole.CHECKOUT),
        ("Оплата стандарт", StageKey.CHECKOUT, Tariff.STANDARD, StepRole.CHECKOUT),
        ("удаление премиум", StageKey.CHURN, Tariff.PREMIUM, StepRole.ACCESS_REMOVAL),
        ("удаление база", StageKey.CHURN, Tariff.BASIC, StepRole.ACCESS_REMOVAL),
        ("удаление стандарт", StageKey.CHURN, Tariff.STANDARD, StepRole.ACCESS_REMOVAL),
        ("Сообщение 9", StageKey.SERVICE, None, StepRole.MESSAGE),
        ("Сообщение 12", StageKey.SERVICE, None, StepRole.MESSAGE),
        ("Задержка 1", StageKey.SERVICE, None, StepRole.DELAY),
        ("Задержка 4", StageKey.SERVICE, None, StepRole.DELAY),
        ("Действия 1", StageKey.SERVICE, None, StepRole.ACTION),
        ("Действия 6 (копия)", StageKey.SERVICE, None, StepRole.ACTION),
        ("Действия 10 (копия)", StageKey.SERVICE, None, StepRole.ACTION),
    ],
)
def test_classify_step_real_titles(title, stage, tariff, role):
    c = classify_step(title)
    assert c.stage == stage
    assert c.tariff == tariff
    assert c.role == role


def test_classify_step_unknown_is_graceful():
    c = classify_step("какой-то новый шаг")
    assert c.stage == StageKey.UNKNOWN
    assert c.tariff is None
    assert c.role == StepRole.UNKNOWN


def test_all_28_real_steps_are_classified_without_unknown():
    """Ни один из 28 реальных шагов не должен падать в UNKNOWN."""
    real_titles = [
        "Приветствие", "Выбор пакета", "Инфо о пакетах 1", "Инфо о пакетах 2",
        "Инфо о пакетах 3", "Оплата премиум", "Оплата базовый", "Сообщение 9",
        "Оплата стандарт", "Действия 1", "Действия 2", "Действия 3", "Действия 4",
        "Действия 6", "Действия 6 (копия)", "Действия 6 (копия)",
        "Действия 6 (копия) (копия)", "Действия 10", "Действия 10 (копия)",
        "Действия 10 (копия)", "удаление стандарт", "удаление база",
        "удаление премиум", "Сообщение 12", "Задержка 1", "Сообщение 15",
        "Задержка 4", "Действия 4 (копия)",
    ]
    assert len(real_titles) == 28
    for t in real_titles:
        assert classify_step(t).stage != StageKey.UNKNOWN, t


# --- parse_payment_tag: теги-оплаты из живых данных -----------------------

@pytest.mark.parametrize(
    "tag, expected",
    [
        ("купил_базовый", Tariff.BASIC),
        ("купил_стандарт", Tariff.STANDARD),
        ("купил_премиум_", Tariff.PREMIUM),  # ровно так лежит в BotHelp
        ("КУПИЛ_БАЗОВЫЙ", Tariff.BASIC),
        ("просто_тег", None),
        ("базовый_без_купил", None),  # нет маркера покупки -> не оплата
        ("", None),
    ],
)
def test_parse_payment_tag(tag, expected):
    assert parse_payment_tag(tag) == expected


# --- derive_subscriber_events: единая событийная модель -------------------

def test_subscriber_without_tags_yields_only_bot_start():
    sub = {"id": 2, "createdAt": 1778267935, "tags": []}
    events = derive_subscriber_events(sub)
    assert [e.event_type for e in events] == ["bot_start"]
    assert events[0].occurred_at == 1778267935


def test_subscriber_with_one_buy_tag_yields_start_and_payment():
    sub = {"id": 3, "createdAt": 1778269560, "tags": ["купил_базовый"]}
    events = derive_subscriber_events(sub)
    assert [e.event_type for e in events] == ["bot_start", "payment"]
    pay = events[1]
    assert pay.tariff == Tariff.BASIC
    assert pay.occurred_at == 1778269560
    assert pay.source_tag == "купил_базовый"


def test_subscriber_with_multiple_buy_tags_yields_one_payment_each():
    sub = {"id": 3, "createdAt": 1778269560, "tags": ["купил_базовый", "купил_стандарт"]}
    events = derive_subscriber_events(sub)
    assert [e.event_type for e in events] == ["bot_start", "payment", "payment"]
    assert {e.tariff for e in events if e.event_type == "payment"} == {Tariff.BASIC, Tariff.STANDARD}


def test_non_purchase_tags_do_not_create_payments():
    sub = {"id": 9, "createdAt": 1778270000, "tags": ["vip", "ручной_тег"]}
    events = derive_subscriber_events(sub)
    assert [e.event_type for e in events] == ["bot_start"]


# --- source_from_subscriber: атрибуция трафика ----------------------------

def test_source_maps_channel_and_collects_utm_and_cuid():
    sub = {
        "channelType": "telegram",
        "channelName": "СамоДвижение | Курс от ЛАПЫЧЕВА",
        "cuid": "dc2s.2",
        "utmSource": "tiktok", "utmCampaign": "spring", "utmMedium": "",
        "utmContent": "", "utmTerm": "",
    }
    src = source_from_subscriber(sub)
    assert src.platform == "telegram"
    assert src.channel_name == "СамоДвижение | Курс от ЛАПЫЧЕВА"
    assert src.cuid == "dc2s.2"
    assert src.utm == {"utmSource": "tiktok", "utmCampaign": "spring"}  # пустые отброшены


def test_source_with_no_utm_is_empty_dict():
    sub = {"channelType": "telegram", "cuid": "dc2s.5", "utmSource": "", "utmCampaign": ""}
    src = source_from_subscriber(sub)
    assert src.utm == {}
    assert src.cuid == "dc2s.5"
