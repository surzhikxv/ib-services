"""Контракт интеграции Prodamus: подпись (HMAC), ссылка, order_id, разбор вебхука.

Эти тесты не ходят в сеть — проверяют чистую логику.
"""
from __future__ import annotations

import asyncio

from bot import payments
from bot.webhook import _listify, _parse_nested, make_webhook_app

SECRET = "test_secret_key"


def test_php_json_escapes_slashes_keeps_unicode():
    # как PHP json_encode($d, JSON_UNESCAPED_UNICODE): компактно, юникод как есть, слэш экранируется
    assert payments._php_json({"a": "http://y/z"}) == '{"a":"http:\\/\\/y\\/z"}'
    assert payments._php_json({"n": "Премиум"}) == '{"n":"Премиум"}'
    # ключи сортируются на шаге sign() (см. test_sign_is_order_independent_for_dict_keys)
    assert payments._sort_keys({"b": "x", "a": "y"}) == {"a": "y", "b": "x"}


def test_sign_verify_roundtrip_and_tamper():
    data = {
        "order_id": "tg42-premium-1700000000",
        "sum": "2990",
        "products": [{"name": "X", "price": "2990"}],
    }
    sig = payments.sign(data, SECRET)
    assert payments.verify(data, sig, SECRET)
    # подмена суммы → подпись не сходится
    tampered = dict(data, sum="1")
    assert not payments.verify(tampered, sig, SECRET)
    # чужой ключ → не сходится
    assert not payments.verify(data, sig, "other")


def test_sign_is_order_independent_for_dict_keys():
    a = payments.sign({"a": "1", "b": "2"}, SECRET)
    b = payments.sign({"b": "2", "a": "1"}, SECRET)
    assert a == b


def test_order_id_roundtrip():
    oid = payments.make_order_id(123456, "standard")
    assert payments.parse_order_id(oid) == (123456, "standard")
    # мусор не валит
    assert payments.parse_order_id("nonsense") == (None, None)
    assert payments.parse_order_id("tg1-unknown-1") == (None, None)


def test_resolve_payment_identity_falls_back_to_customer_extra_and_product():
    data = {
        "order_id": "46411468",
        "customer_extra": "tg:960394602",
        "products": [{"name": "Премиум пакет", "price": "2990.00", "quantity": "1"}],
    }

    assert payments.resolve_payment_identity(data) == (960394602, "premium")


def test_resolve_payment_identity_falls_back_to_sum_when_products_missing():
    data = {"order_id": "46411468", "customer_extra": "tg:42", "sum": "1 699.00"}

    assert payments.resolve_payment_identity(data) == (42, "basic")


def test_build_payment_url_has_order_and_product():
    url = payments.build_payment_url(777, "premium")
    assert url.startswith(f"https://{payments.PRODAMUS_DOMAIN}/?")
    assert "order_id=tg777-premium-" in url
    assert "products%5B0%5D%5Bprice%5D=2990" in url  # products[0][price]=2990 (urlencoded)
    assert "do=pay" in url


def test_payment_data_includes_notification_url_when_public_base_set(monkeypatch):
    monkeypatch.setattr(payments, "PUBLIC_BASE_URL", "https://bot.example")

    data = payments.payment_data(777, "basic")

    assert payments.notification_url() == "https://bot.example/prodamus"
    assert data["urlNotification"] == "https://bot.example/prodamus"


def test_payment_data_without_public_base_has_no_notification_url(monkeypatch):
    monkeypatch.setattr(payments, "PUBLIC_BASE_URL", "")

    data = payments.payment_data(777, "basic")

    assert payments.notification_url() == ""
    assert "urlNotification" not in data


def test_webhook_parses_nested_form_into_products_list():
    items = [
        ("order_id", "tg42-basic-1700000000"),
        ("sum", "1699"),
        ("products[0][name]", "Базовый пакет"),
        ("products[0][price]", "1699"),
        ("products[0][quantity]", "1"),
    ]
    data = _listify(_parse_nested(items))
    assert data["order_id"] == "tg42-basic-1700000000"
    assert isinstance(data["products"], list)
    assert data["products"][0] == {"name": "Базовый пакет", "price": "1699", "quantity": "1"}


def test_webhook_signature_matches_reconstructed_form():
    # эмулируем то, что Prodamus подписывает у себя, и что мы восстанавливаем из формы
    items = [
        ("order_id", "tg42-premium-1700000000"),
        ("sum", "2990"),
        ("payment_status", "success"),
        ("products[0][name]", "Премиум пакет"),
        ("products[0][price]", "2990"),
        ("products[0][quantity]", "1"),
    ]
    data = _listify(_parse_nested(items))
    sig = payments.sign(data, SECRET)
    assert payments.verify(data, sig, SECRET)


def test_webhook_calls_on_paid_for_successful_signed_payment(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(payments, "PRODAMUS_SECRET", SECRET)
    calls = []

    async def on_paid(tg_id, tariff, data):
        calls.append((tg_id, tariff, data))

    async def run():
        items = [
            ("order_id", "tg42-premium-1700000000"),
            ("sum", "2990"),
            ("payment_status", "success"),
            ("products[0][name]", "Премиум пакет"),
            ("products[0][price]", "2990"),
            ("products[0][quantity]", "1"),
        ]
        signed_data = _listify(_parse_nested(items))
        client = TestClient(TestServer(make_webhook_app(on_paid)))
        await client.start_server()
        try:
            resp = await client.post(
                payments.WEBHOOK_PATH,
                data=dict(items),
                headers={"Sign": payments.sign(signed_data)},
            )
            text = await resp.text()
        finally:
            await client.close()
        assert resp.status == 200
        assert text == "success"

    asyncio.run(run())

    assert len(calls) == 1
    tg_id, tariff, data = calls[0]
    assert tg_id == 42
    assert tariff == "premium"
    assert data["order_id"] == "tg42-premium-1700000000"


def test_webhook_calls_on_paid_for_numeric_order_id_with_customer_extra(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(payments, "PRODAMUS_SECRET", SECRET)
    calls = []

    async def on_paid(tg_id, tariff, data):
        calls.append((tg_id, tariff, data))

    async def run():
        items = [
            ("order_id", "46411468"),
            ("customer_extra", "tg:960394602"),
            ("sum", "2990"),
            ("payment_status", "success"),
            ("products[0][name]", "Премиум пакет"),
            ("products[0][price]", "2990.00"),
            ("products[0][quantity]", "1"),
        ]
        signed_data = _listify(_parse_nested(items))
        client = TestClient(TestServer(make_webhook_app(on_paid)))
        await client.start_server()
        try:
            resp = await client.post(
                payments.WEBHOOK_PATH,
                data=dict(items),
                headers={"Sign": payments.sign(signed_data)},
            )
            text = await resp.text()
        finally:
            await client.close()
        assert resp.status == 200
        assert text == "success"

    asyncio.run(run())

    assert len(calls) == 1
    tg_id, tariff, data = calls[0]
    assert tg_id == 960394602
    assert tariff == "premium"
    assert data["order_id"] == "46411468"


def test_webhook_rejects_bad_signature_without_on_paid(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(payments, "PRODAMUS_SECRET", SECRET)
    calls = []

    async def on_paid(tg_id, tariff, data):
        calls.append((tg_id, tariff, data))

    async def run():
        items = [
            ("order_id", "tg42-basic-1700000000"),
            ("sum", "1699"),
            ("payment_status", "success"),
        ]
        client = TestClient(TestServer(make_webhook_app(on_paid)))
        await client.start_server()
        try:
            resp = await client.post(
                payments.WEBHOOK_PATH,
                data=dict(items),
                headers={"Sign": "bad"},
            )
            text = await resp.text()
        finally:
            await client.close()
        assert resp.status == 403
        assert text == "bad signature"

    asyncio.run(run())

    assert calls == []
