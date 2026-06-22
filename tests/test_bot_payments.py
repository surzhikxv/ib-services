"""Контракт интеграции Prodamus: подпись (HMAC), ссылка, order_id, разбор вебхука.

Эти тесты не требуют сырья BotHelp и не ходят в сеть — проверяют чистую логику.
"""
from __future__ import annotations

from bot import payments
from bot.webhook import _listify, _parse_nested

SECRET = "test_secret_key"


def test_php_json_escapes_slashes_keeps_unicode():
    # как PHP json_encode($d, JSON_UNESCAPED_UNICODE): компактно, юникод как есть, слэш экранируется
    assert payments._php_json({"a": "http://y/z"}) == '{"a":"http:\\/\\/y\\/z"}'
    assert payments._php_json({"n": "Премиум"}) == '{"n":"Премиум"}'
    # ключи сортируются на шаге sign() (см. test_sign_is_order_independent_for_dict_keys)
    assert payments._sort_keys({"b": "x", "a": "y"}) == {"a": "y", "b": "x"}


def test_sign_verify_roundtrip_and_tamper():
    data = {"order_id": "tg42-premium-1700000000", "sum": "2990", "products": [{"name": "X", "price": "2990"}]}
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


def test_build_payment_url_has_order_and_product():
    url = payments.build_payment_url(777, "premium")
    assert url.startswith(f"https://{payments.PRODAMUS_DOMAIN}/?")
    assert "order_id=tg777-premium-" in url
    assert "products%5B0%5D%5Bprice%5D=2990" in url  # products[0][price]=2990 (urlencoded)
    assert "do=pay" in url


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
