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
import time
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
    if PUBLIC_BASE_URL:
        data["urlNotification"] = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"
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
