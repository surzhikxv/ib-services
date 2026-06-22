"""Приём вебхука об оплате от Prodamus (aiohttp).

Prodamus после успешной оплаты шлёт POST (form-urlencoded) на urlNotification с
заголовком `Sign` = HMAC-SHA256 всех полей секретным ключом магазина. Здесь мы:
  1) восстанавливаем вложенную структуру полей (products[0][name] → …);
  2) сверяем подпись (без неё оплате доверять нельзя);
  3) разбираем order_id → tg_id + тариф;
  4) на успешной оплате вызываем on_paid(...) — выдать доступ и дослать страницу «оплачено»;
  5) отвечаем 200.

Логика «что делать после оплаты» вынесена в коллбэк on_paid — модуль не знает про бота.
"""
from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable

from aiohttp import web

from .payments import WEBHOOK_PATH, parse_order_id, sign, verify

logger = logging.getLogger("bot.webhook")

# (tg_id, tariff, data) → None. Вызывается только на подтверждённой успешной оплате.
OnPaid = Callable[[int, str, dict], Awaitable[None]]

_SUCCESS_STATUSES = {"success", "succeeded", "paid"}


def _parse_nested(items: list[tuple[str, str]]) -> dict:
    """`products[0][name]=x` → {"products": {"0": {"name": "x"}}}."""
    root: dict = {}
    for key, val in items:
        parts = re.findall(r"[^\[\]]+", key)
        cur = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                cur[part] = val
            else:
                cur = cur.setdefault(part, {})
    return root


def _listify(obj):
    """Словарь с ключами-индексами {"0":..,"1":..} → список (как массив в PHP $_POST)."""
    if isinstance(obj, dict):
        obj = {k: _listify(v) for k, v in obj.items()}
        keys = list(obj.keys())
        if keys and all(k.isdigit() for k in keys):
            return [obj[k] for k in sorted(keys, key=int)]
        return obj
    return obj


def make_webhook_app(on_paid: OnPaid) -> web.Application:
    async def handle(request: web.Request) -> web.Response:
        form = await request.post()
        data = _listify(_parse_nested(list(form.items())))
        signature = request.headers.get("Sign") or request.headers.get("sign")
        if signature is None and isinstance(data, dict):
            signature = data.pop("signature", "")  # на случай подписи в теле

        if not verify(data, signature):
            logger.warning(
                "Prodamus: подпись не сошлась. header=%s computed=%s",
                signature, sign(data),
            )
            return web.Response(status=403, text="bad signature")

        order_id = str(data.get("order_id", "")) if isinstance(data, dict) else ""
        tg_id, tariff = parse_order_id(order_id)
        status = str(data.get("payment_status", "") if isinstance(data, dict) else "").lower()

        if tg_id is None or tariff is None:
            logger.warning("Prodamus: не разобран order_id=%r", order_id)
            return web.Response(text="ok")  # подпись валидна, но заказ чужой — не падаем
        if status and status not in _SUCCESS_STATUSES:
            logger.info("Prodamus: оплата %s статус=%s — пропускаем", order_id, status)
            return web.Response(text="ok")

        logger.info("Prodamus: оплата подтверждена tg=%s тариф=%s order=%s", tg_id, tariff, order_id)
        try:
            await on_paid(tg_id, tariff, data if isinstance(data, dict) else {})
        except Exception:  # noqa: BLE001 — Prodamus должен получить 200, иначе будет ретраить
            logger.exception("Prodamus: ошибка в обработке оплаты %s", order_id)
        return web.Response(text="success")

    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle)
    app.router.add_get("/health", lambda _r: web.Response(text="ok"))
    return app
