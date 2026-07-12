"""Prodamus: платёжная ссылка, подпись (HMAC) и разбор order_id.

Магазин один (`<домен>.payform.ru`), секретный ключ один, товаров три (по тарифу).
Ссылку строим динамически и кладём в неё `order_id`, в котором зашит Telegram-id
человека и тариф — чтобы вебхук об оплате однозначно сопоставился с пользователем.

Подпись — по официальному алгоритму Prodamus (класс Hmac): все значения приводим к
строкам, рекурсивно сортируем ключи, сериализуем в компактный JSON (юникод не
экранируется, прямой слэш экранируется как в PHP `json_encode`), затем HMAC-SHA256.

Секреты берём ТОЛЬКО из окружения (.env), в код не зашиваем.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

# --- Конфиг магазина (из .env) ----------------------------------------------
PRODAMUS_DOMAIN = os.getenv("PRODAMUS_DOMAIN", "samodvizhenieslapychev.payform.ru")
PRODAMUS_SECRET = os.getenv("PRODAMUS_SECRET", "")

# Публичный адрес, на который Prodamus пришлёт вебхук об оплате (туннель/домен).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
WEBHOOK_PATH = "/prodamus"

# Куда вернуть человека после оплаты в браузере (например, ссылка на бота).
PAYMENT_RETURN_URL = os.getenv("PAYMENT_RETURN_URL", "")

# Подписывать ли исходящую ссылку (защита цены от подмены в URL). По умолчанию выкл —
# включим после того, как на туннеле подтвердим, что приём вебхука и подпись сходятся.
SIGN_LINKS = os.getenv("PRODAMUS_SIGN_LINKS", "").strip() in {"1", "true", "yes"}

# Тариф → товар Prodamus (цена в рублях, наименование как в кабинете магазина).
TARIFFS: dict[str, dict[str, str]] = {
    "basic": {"price": "1699", "name": "Базовый пакет"},
    "standard": {"price": "1990", "name": "Стандарт +"},
    "premium": {"price": "2990", "name": "Премиум пакет"},
}

_ORDER_SEP = "-"


def configured() -> bool:
    """Готов ли Prodamus к боевой работе (есть домен и секрет)."""
    return bool(PRODAMUS_DOMAIN and PRODAMUS_SECRET)


def notification_url() -> str:
    """Публичный URL, на который Prodamus должен прислать webhook после оплаты."""
    if not PUBLIC_BASE_URL:
        return ""
    return f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"


def checkout_signature(tg_id: int, tariff: str, nonce: str, secret: str = "") -> str:
    """Sign the analytics redirect so outsiders cannot forge checkout events."""
    key = (secret or PRODAMUS_SECRET).encode()
    payload = f"{tg_id}:{tariff}:{nonce}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_checkout(tg_id: int, tariff: str, nonce: str, signature: str) -> bool:
    if tariff not in TARIFFS or not PRODAMUS_SECRET:
        return False
    expected = checkout_signature(tg_id, tariff, nonce)
    return hmac.compare_digest(expected, (signature or "").strip())


# --- order_id: зашиваем tg_id и тариф ----------------------------------------

def make_order_id(tg_id: int, tariff: str) -> str:
    return f"tg{tg_id}{_ORDER_SEP}{tariff}{_ORDER_SEP}{int(time.time())}"


def parse_order_id(order_id: str) -> tuple[int | None, str | None]:
    """Разобрать order_id обратно в (tg_id, tariff). Терпим к мусору."""
    try:
        head, tariff, _ts = order_id.split(_ORDER_SEP, 2)
        if head.startswith("tg") and tariff in TARIFFS:
            return int(head[2:]), tariff
    except (ValueError, AttributeError):
        pass
    return None, None


def parse_customer_extra(data: dict) -> int | None:
    """Достать tg_id из customer_extra вида `tg:123`.

    Prodamus может прислать в webhook свой числовой order_id вместо нашего
    `tg<id>-<tariff>-<ts>`. Поэтому в ссылку дополнительно кладём customer_extra.
    """
    extra = str(data.get("customer_extra", "") if isinstance(data, dict) else "")
    match = re.search(r"(?:^|[^\w])tg:?(\d+)(?:$|[^\w])", extra)
    return int(match.group(1)) if match else None


def _amount_key(value) -> str:
    text = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return ""
    try:
        amount = Decimal(match.group(0))
    except InvalidOperation:
        return ""
    if amount == amount.to_integral_value():
        return str(int(amount))
    return str(amount.normalize())


def tariff_from_payment_data(data: dict) -> str | None:
    """Определить тариф из webhook по товару/цене, если order_id не содержит тариф."""
    if not isinstance(data, dict):
        return None

    by_name = {cfg["name"].casefold(): key for key, cfg in TARIFFS.items()}
    by_price = {_amount_key(cfg["price"]): key for key, cfg in TARIFFS.items()}
    products = data.get("products") or []
    if isinstance(products, dict):
        products = [products[k] for k in sorted(products) if isinstance(products[k], dict)]
    if isinstance(products, list):
        for product in products:
            if not isinstance(product, dict):
                continue
            name = str(product.get("name", "")).casefold()
            if name in by_name:
                return by_name[name]
            price_key = _amount_key(product.get("price") or product.get("sum") or product.get("amount"))
            if price_key in by_price:
                return by_price[price_key]

    amount_key = _amount_key(data.get("sum") or data.get("amount"))
    return by_price.get(amount_key)


def resolve_payment_identity(data: dict) -> tuple[int | None, str | None]:
    """Разобрать, кому и какой тариф выдать по webhook Prodamus."""
    order_id = str(data.get("order_id", "") if isinstance(data, dict) else "")
    tg_id, tariff = parse_order_id(order_id)
    return tg_id or parse_customer_extra(data), tariff or tariff_from_payment_data(data)


# --- Подпись Prodamus (HMAC) -------------------------------------------------

def _stringify(obj):
    if isinstance(obj, dict):
        return {k: _stringify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify(v) for v in obj]
    if isinstance(obj, bool):
        return "1" if obj else "0"
    if obj is None:
        return ""
    return str(obj)


def _sort_keys(obj):
    if isinstance(obj, dict):
        return {k: _sort_keys(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_sort_keys(v) for v in obj]
    return obj


def _php_json(data) -> str:
    """JSON как у PHP json_encode($d, JSON_UNESCAPED_UNICODE): компактно, юникод не
    экранируется, прямой слэш экранируется (\\/)."""
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return s.replace("/", "\\/")


def sign(data: dict, secret: str = "") -> str:
    """HMAC-SHA256 по алгоритму Prodamus над данными (без поля signature)."""
    secret = secret or PRODAMUS_SECRET
    prepared = _sort_keys(_stringify(data))
    return hmac.new(secret.encode(), _php_json(prepared).encode(), hashlib.sha256).hexdigest()


def verify(data: dict, signature: str, secret: str = "") -> bool:
    """Сверить подпись входящего вебхука. data — без поля signature."""
    expected = sign(data, secret)
    return hmac.compare_digest(expected, (signature or "").strip())


# --- Построение платёжной ссылки --------------------------------------------

def _flatten(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    """{"products":[{"name":"x"}]} → [("products[0][name]","x")] для query string."""
    out: list[tuple[str, str]] = []
    for k, v in data.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out.extend(_flatten(v, key))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                ik = f"{key}[{i}]"
                if isinstance(item, dict):
                    out.extend(_flatten(item, ik))
                else:
                    out.append((ik, str(item)))
        else:
            out.append((key, str(v)))
    return out


def payment_data(tg_id: int, tariff: str) -> dict:
    """Параметры платежа (вложенная структура — её же подписываем)."""
    cfg = TARIFFS[tariff]
    data: dict = {
        "order_id": make_order_id(tg_id, tariff),
        "customer_extra": f"tg:{tg_id}",
        "products": [{"name": cfg["name"], "price": cfg["price"], "quantity": "1"}],
        "do": "pay",
    }
    notify_url = notification_url()
    if notify_url:
        data["urlNotification"] = notify_url
    if PAYMENT_RETURN_URL:
        data["urlReturn"] = PAYMENT_RETURN_URL
        data["urlSuccess"] = PAYMENT_RETURN_URL
    return data


def build_payment_url(tg_id: int, tariff: str) -> str:
    """Готовая платёжная ссылка Prodamus с зашитым order_id (и подписью, если включена)."""
    data = payment_data(tg_id, tariff)
    pairs = _flatten(data)
    if SIGN_LINKS and PRODAMUS_SECRET:
        pairs.append(("signature", sign(data)))
    return f"https://{PRODAMUS_DOMAIN}/?{urlencode(pairs)}"


def build_checkout_url(tg_id: int, tariff: str) -> str:
    """One-tap tracked redirect to Prodamus; falls back to the direct payment URL."""
    if not PUBLIC_BASE_URL or not PRODAMUS_SECRET:
        return build_payment_url(tg_id, tariff)
    nonce = str(int(time.time()))
    query = urlencode({
        "checkout": "1",
        "tg_id": str(tg_id),
        "tariff": tariff,
        "nonce": nonce,
        "signature": checkout_signature(tg_id, tariff, nonce),
    })
    return f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}?{query}"
