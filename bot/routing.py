"""Граф переходов воронки, восстановленный из сырья BotHelp.

Кнопки BotHelp переходят через `button.actions[].run_bot.value` — это `referral`
целевого шага. Часть кнопок ведёт в служебные шаги (action/delay) без контента;
они, в свою очередь, маршрутизируют дальше через `followupActions.actions[].run_bot`.
Здесь мы «проматываем» такие служебные звенья и получаем ближайший контентный шаг.

Результат — таблица маршрутов: для каждой кнопки контентного шага известно, что
делать при нажатии:
  • Route("step", target=i)   — показать контентный шаг i;
  • Route("url",  url=...)     — открыть ссылку (рабочие t.me-кнопки «Перейти в канал»);
  • Route("pay",  tariff=...)  — кнопка «Оплата»: ссылку подставит конфиг bot/links.py;
  • Route("terminal")          — ветка завершается служебным шагом без контента.

Тексты/подписи кнопок не трогаем — это делает bot/content.py. Здесь только логика переходов.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .content import RAW_PATH

# Шаги с инфо о пакете → ключ тарифа (по нему берём платёжную ссылку и страницу «оплачено»).
TARIFF_BY_INFO_STEP = {2: "basic", 3: "standard", 4: "premium"}

# Шаги, достижимые кликом через on_button → канонический этап (для ярлыка step_enter).
# Шаг 0 (приветствие) достижим и через /start (→ bot_start=welcome), и кликом «Назад»
# со step 7 (→ step_enter) — маппим в welcome, чтобы возврат не давал NULL-этап.
# Шаги 5/6/8 («оплачено») идут через on_paid без step_enter — их закрывает payment.
STAGE_BY_STEP = {
    0: "welcome", 7: "welcome",
    1: "package_choice",
    2: "package_info", 3: "package_info", 4: "package_info",
}

# Куда вести после успешной оплаты (страницы «Оплата прошла»). Пока оплата не подключена,
# это нужно для будущей привязки платёжного вебхука и для режима симуляции (см. bot/links.py).
CONFIRM_STEP_BY_TARIFF = {"basic": 6, "standard": 8, "premium": 5}


@dataclass(frozen=True)
class Route:
    kind: str  # "step" | "url" | "pay" | "terminal"
    target: int | None = None
    url: str | None = None
    tariff: str | None = None


def _load_raw() -> dict:
    return json.loads(Path(RAW_PATH).read_text(encoding="utf-8"))


def _is_content(step: dict) -> bool:
    return bool((step.get("flowData") or {}).get("steps"))


def _run_bot_value(actions: list | None) -> str | None:
    for a in actions or []:
        if a.get("action") == "run_bot":
            return str(a.get("value"))
    return None


def build_routes(raw: dict | None = None) -> dict[tuple[int, int, int], Route]:
    """Собрать таблицу маршрутов { (step, block, button) -> Route }."""
    raw = raw or _load_raw()
    steps = raw.get("steps", [])
    ref2idx = {str(s.get("referral")): i for i, s in enumerate(steps)}

    def resolve_postback(start_ref: str) -> Route:
        """Промотать служебные шаги до ближайшего контентного."""
        seen: set[int] = set()
        i = ref2idx.get(start_ref)
        while i is not None and i not in seen:
            seen.add(i)
            if _is_content(steps[i]):
                return Route("step", target=i)
            nxt = _run_bot_value((steps[i].get("followupActions") or {}).get("actions"))
            i = ref2idx.get(nxt) if nxt else None
        return Route("terminal")

    routes: dict[tuple[int, int, int], Route] = {}
    for si, step in enumerate(steps):
        if not _is_content(step):
            continue
        tariff = TARIFF_BY_INFO_STEP.get(si)
        for bi, block in enumerate((step.get("flowData") or {}).get("steps") or []):
            for ki, btn in enumerate(block.get("buttons") or []):
                key = (si, bi, ki)
                if btn.get("type") == "web_url":
                    url = btn.get("url") or ""
                    if url.startswith(("http://", "https://")):
                        routes[key] = Route("url", url=url)
                    else:  # динамическая переменная {%payment_N%} — это кнопка «Оплата»
                        routes[key] = Route("pay", tariff=tariff)
                    continue
                ref = _run_bot_value(btn.get("actions"))
                routes[key] = resolve_postback(ref) if ref else Route("terminal")
    return routes


# Точка входа воронки — шаг приветствия.
ENTRY_STEP = 0
