"""Contract of explicit routes in the owned funnel snapshot."""
from __future__ import annotations

from bot.content import load_steps
from bot.routing import ENTRY_STEP, build_routes

def test_entry_is_welcome():
    assert ENTRY_STEP == 0
    assert load_steps()[ENTRY_STEP].title == "Приветствие"


def test_core_funnel_transitions():
    R = build_routes()
    # Приветствие → видео-приветствие
    assert R[(0, 0, 0)].kind == "step" and R[(0, 0, 0)].target == 7
    # Видео: Продолжить → выбор пакета, Назад → приветствие
    assert R[(7, 1, 0)].target == 1
    assert R[(7, 1, 1)].target == 0
    # Выбор пакета → инфо базовый/стандарт/премиум, Назад → видео
    assert R[(1, 0, 0)].target == 2
    assert R[(1, 0, 1)].target == 3
    assert R[(1, 0, 2)].target == 4
    assert R[(1, 0, 3)].target == 7
    # Инфо-страницы: Назад → выбор пакета
    for s in (2, 3, 4):
        assert R[(s, 0, 1)].target == 1


def test_payment_buttons_are_pay_routes_with_tariff():
    R = build_routes()
    assert R[(2, 0, 0)].kind == "pay" and R[(2, 0, 0)].tariff == "basic"
    assert R[(3, 0, 0)].kind == "pay" and R[(3, 0, 0)].tariff == "standard"
    assert R[(4, 0, 0)].kind == "pay" and R[(4, 0, 0)].tariff == "premium"


def test_channel_buttons_are_real_urls_and_apply_terminal():
    R = build_routes()
    # «Перейти в канал» — рабочие t.me-ссылки
    for s in (5, 6, 8):
        assert R[(s, 0, 1)].kind == "url" and R[(s, 0, 1)].url.startswith("https://t.me/")
        # «Назад к пакетам» → выбор пакета
        assert R[(s, 0, 0)].target == 1
        # «Подал заявку» завершается служебным шагом без контента
        assert R[(s, 0, 2)].kind == "terminal"
