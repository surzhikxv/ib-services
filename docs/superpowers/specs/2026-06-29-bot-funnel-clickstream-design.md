# Per-user funnel clickstream → lake (design)

> Дата: 2026-06-29. Ветка: `deploy/bot-prodamus`. Развивает план
> `2026-06-26-bot-funnel-events.md` (bot_start/step_enter/payment уже в проде).

## Цель

Бот (@SamodvijenieBot) пишет в озеро **полный, по-юзерный, append-only** поток
событий каждого шага воронки — с осмысленными ярлыками этапов, атрибуцией из
deep-link `/start` и базовой личностью подписчика.

**Жёсткие свойства, которые не нарушаем:**

- **Без миграции БД.** Всё ложится на существующие колонки/справочники
  (`events.{source_id,funnel_stage_id,tariff_id,raw,occurred_at}`,
  `subscribers.{name,source_id,raw,last_seen_at}`, `sources`, 8 засеянных
  `funnel_stages`). `docs/migrations.md`: `create_all` НЕ добавляет колонки —
  поэтому новых колонок и нет.
- **Best-effort.** Любая запись в озеро обёрнута `_emit` (`asyncio.to_thread` +
  глотание исключений). Недоступность/отсутствие схемы озера НЕ блокирует воронку
  и ответ вебхуку Prodamus.
- **Платёжный путь не трогаем (решение B1).** Кнопка «Оплата» остаётся прямой
  URL-ссылкой Prodamus. Её клик Telegram открывает в браузере и боту он НЕ виден —
  отдельного `checkout`-события нет. Сигнал «дошёл до экрана оплаты» = `step_enter`
  на `package_info` (шаги 2/3/4). Этап `checkout` остаётся пустым — это известное
  ограничение URL-кнопки, не баг.

## Текущее состояние (от чего отталкиваемся)

`kontur/ingest.py` + `bot/bot.py` уже пишут три типа событий через `_emit`:

- `bot_start` — на `/start`, `dedup_key = tg{id}:bot_start`, stage=`welcome`.
- `step_enter` — на каждый клик-переход (`route.kind=="step"`),
  `dedup_key = tg{id}:step:{N}`, stage/tariff только для пакетов 2/3/4.
- `payment` — по вебхуку Prodamus, `dedup_key = tg{id}:payment:{order_id}`,
  stage=`paid`.

**Дыры, которые закрываем:**

1. `dedup_key = (user, step)` схлопывает повторные заходы (upsert перезаписывает
   `occurred_at`) → не видно реального пути: повторов, возвратов «Назад», порядка,
   времени на шаге.
2. Шаги 7 (видео) и 1 (выбор пакета) не несут `stage` → воронка читается только по
   «магическим» индексам шага.
3. Нет атрибуции (`/start <payload>` не ловится; `subscriber.source_id` пуст) и
   личности (пишем только `tg_user_id`).
4. Терминальная кнопка «Подал заявку» (`route.kind=="terminal"`) не пишется
   (сейчас `pass`).

## Таксономия событий (итоговая)

| event_type   | когда                              | dedup_key                          | stage           | поля |
|--------------|------------------------------------|------------------------------------|-----------------|------|
| `bot_start`  | `/start`                           | `tg{id}:start:m{message_id}`       | `welcome`       | `source_id` (если payload), обогащение `name`/`username` |
| `step_enter` | клик-переход (`route.kind=="step"`)| `tg{id}:step:{N}:cq{callback_id}`  | `STAGE_BY_STEP[N]` | `tariff` для 2/3/4 |
| `applied`    | терминал «Подал заявку»            | `tg{id}:applied:cq{callback_id}`   | `paid`          | `raw={"button":title,"step":N}` |
| `payment`    | вебхук Prodamus                    | `tg{id}:payment:{order_id}`        | `paid`          | без изменений (idempotency по `order_id`) |

**Почему Telegram-родные id в ключе.** `callback_query.id` (клики) и `message_id`
(`/start`) — уникальны и стабильны: при ре-доставке апдейта Telegram присылает тот
же id → дубля нет (идемпотентность сохраняется). При этом каждый отдельный клик
несёт свой id → поток append-only (повторы и возвраты — отдельные строки). Прежний
формат `tg{id}:step:{N}` сосуществует со старыми строками (другой формат → нет
коллизий по `UNIQUE(source_system, dedup_key)`); бэкфилл не нужен.

`dedup_key` — `VARCHAR(500)`, длины Telegram-id с запасом помещаются.

## Маппинг шагов на этапы

`bot/routing.py`:

```python
# Шаги, достижимые кликом через on_button → канонический этап.
# 0 достижим через /start (bot_start=welcome) И кликом «Назад» со step 7 (step_enter) —
# маппим в welcome, чтобы возврат не давал NULL-этап. 5/6/8 («оплачено») идут через
# on_paid без step_enter — их закрывает событие payment.
STAGE_BY_STEP = {
    0: "welcome", 7: "welcome",
    1: "package_choice",
    2: "package_info", 3: "package_info", 4: "package_info",
}
```

`TARIFF_BY_INFO_STEP = {2: "basic", 3: "standard", 4: "premium"}` — уже есть,
переиспользуем для tariff на `package_info`.

## Атрибуция (`/start <payload>`)

- `cmd_start` получает `command: CommandObject`; `payload = (command.args or "").strip()`.
- Конвенция payload (Telegram разрешает только `[A-Za-z0-9_-]`, ≤64): пары
  `ключ-значение`, разделённые `_`, ключ от значения — `-`:
  `s-ig_m-cpc_c-july` → `{utm_source:ig, utm_medium:cpc, utm_campaign:july}`.
  Алиасы ключей: `s→utm_source, m→utm_medium, c→utm_campaign, t→utm_term, ct→utm_content`.
  Парсинг **best-effort**: не распознано — `Source.code` хранит payload дословно,
  UTM-поля пустые.
- Распарсенные UTM → `kontur.connectors.utm.normalize_utm(...)` → канонический
  `Source.code` (совпадает с UTM контент-коннекторов).
- `Source(kind="start_link", code=...)` upsert по `(kind, code)`; `source_id`
  привязывается к `subscriber.source_id` И к событию `bot_start.source_id`.
- `source_id` ставится **только когда payload есть** — при «голом» `/start` поле не
  трогаем (не затираем ранее пришедший источник).
- Источник бота — `kind="start_link"`; контент-коннекторы пишут `kind="utm"`.
  Натуральный ключ `sources` — `(kind, code)`, поэтому при одинаковом `code` это
  РАЗНЫЕ строки. Склейка «контент → подписчик» в дашборде — join по нормализованному
  `code`, не по `source_id`/`kind`.

## Личность

На `/start` из `message.from_user`:

- `subscriber.name = f"{first_name} {last_name}".strip()` (когда есть).
- `username` → `subscriber.raw = {"username": ...}` (отдельной колонки нет — кладём
  в `raw`). Пишется только на `bot_start`; `raw` у `telegram_bot`-подписчика
  принадлежит этому пути (другие коннекторы этих строк не трогают), поэтому замена
  всего JSON безопасна.
- `subscriber.last_seen_at = now` — на **каждом** событии (дёшево, полезно).

Момент входа в воронку (`subscribed_at`) отдельно не храним — он выводится как
`MIN(occurred_at)` по `bot_start`-событиям подписчика (append-only).

Обогащение идёт только теми ключами, что переданы: `record_step_enter` не передаёт
`name`/`username` → не затирает их в `null` (upsert пишет только переданный `values`).

## API `kontur/ingest.py` (рефактор)

Ядро принимает **полный** `dedup_key` (как в исходном коде); обёртки строят его из
`uid`. Обогащение подписчика — новые опциональные параметры.

```python
record_funnel_event(session_factory=None, *, tg_id, event_type, dedup_key,
                    stage_key=None, tariff_key=None, occurred_at=None,
                    amount=None, currency=None, raw=None,
                    name=None, username=None, source_code=None) -> None
# - upsert Subscriber (tg_user_id + last_seen_at всегда; name/raw{username}/source_id
#   — только когда переданы; иначе ключ не попадает в values и не затирается)
# - _resolve_source(session, source_code) -> source_id (upsert Source, если код есть)
# - resolve stage_key→funnel_stage_id, tariff_key→tariff_id
# - upsert Event по (source_system, dedup_key), set source_id

record_bot_start(tg_id, *, uid, name=None, username=None, source_code=None,
                 session_factory=None)            # dedup tg{id}:start:{uid}; stage=welcome
record_step_enter(tg_id, step_index, *, uid, stage_key=None, tariff_key=None,
                  session_factory=None)            # dedup tg{id}:step:{N}:{uid}
record_applied(tg_id, step_index, button_title, *, uid, session_factory=None)
                                                   # dedup tg{id}:applied:{uid}; stage=paid
record_payment(tg_id, tariff, order_id, *, amount=None, currency=None,
               raw=None, session_factory=None)     # БЕЗ изменений (order-based key)
```

`uid` — Telegram-родный токен (`str`): `f"m{message_id}"` для start, `f"cq{call.id}"`
для кликов. Обёртки подставляют его в `dedup_key`.

`_resolve_source(session, code)` — upsert `Source` по `(kind="start_link", code)`,
вернуть `id`; `code=None`/пусто → `None`.

## Wiring `bot/bot.py`

- Импорт: добавить `CommandObject` уже есть (используется в `/step`); добавить
  `STAGE_BY_STEP` в импорт из `.routing`.
- `cmd_start(message, command: CommandObject)`:
  ```python
  await send_step(...)                                   # воронка ПЕРВОЙ
  u = message.from_user
  name = " ".join(filter(None, [u.first_name, u.last_name])).strip() or None
  await _emit(ingest.record_bot_start, message.chat.id,
              uid=f"m{message.message_id}", name=name,
              username=u.username, source_code=(command.args or "").strip() or None)
  ```
- `on_button`, ветка `route.kind == "step"` (после `send_step`):
  ```python
  await _emit(ingest.record_step_enter, call.message.chat.id, route.target,
              uid=f"cq{call.id}",
              stage_key=STAGE_BY_STEP.get(route.target),
              tariff_key=TARIFF_BY_INFO_STEP.get(route.target))
  ```
- `on_button`, ветка `route.kind == "terminal"` (вместо `pass`):
  ```python
  si, bi, ki = int(si), int(bi), int(ki)   # уже распарсены из call.data выше
  title = STEPS[si].blocks[bi].buttons[ki].title  # под guard от IndexError
  await _emit(ingest.record_applied, call.message.chat.id, si, title, uid=f"cq{call.id}")
  ```
- `_emit`, `_record_payment`, событие `record_payment` в `on_paid` — **без изменений**.

## Тесты (TDD)

`tests/test_ingest.py` (расширить):

- append-only: два `record_step_enter` одного шага с РАЗНЫМИ `uid` → 2 строки;
- идемпотентность: тот же `uid` → 1 строка;
- `STAGE_BY_STEP`/tariff резолвятся в FK;
- атрибуция: `source_code` → upsert `Source`, `subscriber.source_id` и
  `event.source_id` проставлены; повторный `bot_start` без payload не затирает `source_id`;
- личность: `name`/`username`(в `raw`)/`last_seen_at` записаны;
- `record_applied`: event_type=`applied`, stage=`paid`, `raw` содержит button/step;
- парсинг payload-конвенции `s-ig_m-cpc_c-july` → канонический `Source.code`.

`tests/test_bot_events.py` (расширить, мок `ingest`):

- `cmd_start` прокидывает `uid=m{message_id}`, `name`, `username`, `source_code` из payload;
- step-ветка прокидывает `stage_key`/`tariff_key`/`uid=cq{id}` по `route.target`;
- terminal-ветка зовёт `record_applied` с title/step; не падает при кривом индексе;
- `_emit` best-effort — без регрессий.

Весь набор (`./.venv/bin/python -m pytest`) держим зелёным.

## Деплой (после мёржа)

Памятка deploy-hygiene: перед прод-пушем проверить git/alembic/файловое состояние
прода и собрать найденный WIP в один коммит.

1. **Миграция: нет** (новых колонок нет; этапы засеяны; `sources` существует).
2. Рестарт процесса бота на `72.56.7.154` (ветка `deploy/bot-prodamus`).
3. Живая проверка: `/start` (+ deep-link с payload) и клики по шагам → строки в
   `events` с новыми ключами `tg{id}:start:m…` / `tg{id}:step:{N}:cq…`; у подписчика
   заполнены `name`/`source_id`.
4. Конвенцию payload задокументировать в `docs/bot.md` (раздел для маркетинговых ссылок).

## Вне объёма (YAGNI)

- Захват клика по «Оплата» через свой redirect-эндпоинт (B2) — отклонено: не ставим
  свой сервис в путь к деньгам. Этап `checkout` остаётся пустым.
- Бэкфилл старых `tg{id}:step:{N}` событий в новый формат — не нужен.
- Отдельное событие просмотра страниц «оплачено» (5/6/8) — избыточно, `payment` их
  закрывает.
- Захват клика «Перейти в канал» (URL-кнопка, как и оплата — невидим) — не нужен,
  доступ выдаётся инвайтом в `on_paid`.
