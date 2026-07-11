# Дизайн: коннекторы каналов (A) + доведение фундамента (C)

**Дата:** 2026-06-25
**Проект:** Контур роста — MVP «озеро данных + ИИ над ним» для инфобизнеса (курсы реабилитации ДЦП, клиент Сергей Лапычев).
**Ветка-предшественник:** `deploy/bot-prodamus` (воронка legacy funnel platform → наш aiogram, оплаты Prodamus).
**Метод:** дизайн собран многоагентным ресёрчем (по каждому каналу — агент-исследователь + агент-скептик на подводные камни), решения по развилкам приняты владельцем.

---

## 1. Контекст и цель

Сейчас озеро наполнено только со стороны **воронки/продаж** (исторически — коннектор legacy funnel platform из Phase 1; теперь источник воронки — наш бот). Половина «контент → бот → оплата» — со стороны **трафика и контента** (TikTok, VK, Instagram, Telegram-канал, YouTube) — пустая. Без неё ИИ-аналитик видит только хвост (продажи), но не голову (какой контент даёт трафик). Эта фаза наполняет вторую половину и доводит фундамент, чтобы события воронки писались напрямую из нашего бота, а ИИ-аналитик заработал вживую через офшорный релей.

**Цель A:** единый контракт коннекторов + 5 коннекторов каналов, наполняющих `channels / content / content_metric / sources / events`.
**Цель C:** (1) бот пишет события воронки прямо в озеро; (2) релей LLM доведён и проверен; (3) второй бот — кабинет аналитики `@lapychevanalyticbot`.

**Вне рамок этой фазы:** поиск партнёров, мониторинг ниши, контент-помощник (нарезка видео — Phase 2), полный продакшн-хардненинг.

---

## 2. Зафиксированные решения (развилки закрыты владельцем)

1. **Тайм-серия метрик контента → отдельная таблица `content_metric`** (одна строка на контент/день). Таблица `events` и существующая аналитика (ИИ-дайджест, вьюхи Metabase) остаются чистыми — снапшоты их НЕ раздувают.
2. **Telegram-статистика → отдельный «компанейский» аккаунт** под Telethon-сессию (не личный аккаунт владельца). Сессия = полный доступ к аккаунту, поэтому изолируем.
3. **Реализуем все каналы параллельно.** Оговорка по внешним стенам: у Instagram App Review 4–6 недель — бумаги и dev-режим стартуют сразу, прод-доступ догоняет.
4. **Кабинет-бот показывает ВСЕ метрики**; ИИ-разбор — **еженедельно (по расписанию) + по команде** `/разбор`. Обычные запросы отдают цифры из вьюх (дёшево), модель тратится только на разбор.

---

## 3. Карта доступа и почвы (КРИТИЧНО — исправление)

Главная находка скептика: **РФ с ~апреля 2026 блокирует/душит Telegram (Roskomnadzor, DPI).** Это подтверждается нашим же коммитом `556e3c3` (api.telegram.org с РФ-хостинга — без IPv6-маршрута, «дёрганый» IPv4). Поэтому Telegram-трафик идёт **через офшорный релей**, как и Anthropic/Meta. РФ-родным без релея остаётся **только VK**.

| Канал | Доступ | Почва (egress) | Скорость готовности |
|---|---|---|---|
| **VK** | Официальный API, 2 токена | **РФ-VPS напрямую, БЕЗ релея** (VK глушится ВНЕ РФ) | Быстро |
| **Telegram-канал** | MTProto user-сессия (Telethon) + Bot API | **Через офшорный релей** (РФ душит TG) | Средне |
| **TikTok** | Парсер выгрузок (офиц. API органику не отдаёт) | Не сетевой — файл | Быстро (нужен образец) |
| **Instagram** | Graph API (Business/Creator) + App Review | **Только офшор** (Meta запрещена/заблокирована в РФ) | Медленно (ревью 4–6 нед) |
| **YouTube** | Data API v3 + Analytics API (OAuth) | Офшорный релей | Позже |

> **Per-connector egress policy, НЕ глобальный флаг.** VK ломается через прокси; всем остальным прокси нужен. Каждый клиент принимает опциональный `proxy_url`; VK его не использует.

---

## 4. Единый контракт коннекторов (фундамент A)

### 4.1 Базовый класс
`kontur/connectors/base.py` сейчас — пустой скелет (`name` + абстрактный `sync(session_factory)`), и legacy funnel platform его даже **не наследует** (там свободная функция `sync_legacy_funnel(client, …, bot_referral)`). Делаем настоящий ABC по template-method:

- клиент и конфиг → в `__init__` (решает рассинхрон сигнатур);
- база владеет жизненным циклом: открыть `SyncRun(status='running')` → вызвать хук подкласса `ingest(session, run, stats)` → на успехе `ok` + stats + commit, на исключении rollback + `error` + re-raise;
- общие помощники на базе: `_land_raw(...)` (обёртка над upsert в `raw_records`), `_ts(unix)`.
- Мёртвый legacy funnel platform-коннектор на новый ABC НЕ мигрируем (он больше не запускается). Папка `legacy_funnel/` остаётся только как структурный образец раскладки; ABC валидируется новыми контент-коннекторами и их тестами.

### 4.2 Раскладка каждого коннектора (зеркало `legacy_funnel/`)
`kontur/connectors/<name>/`: `__init__.py`, `client.py` (только сеть: auth + пагинация/бэкофф), `mapping.py` (чистые функции, без сети/БД), `sync.py` (оркестрация). Тесты: `test_<name>_client.py` (httpx MockTransport), `test_<name>_mapping.py` (на канонических JSON-фикстурах), `test_<name>_sync.py` (FakeClient + SQLite).

### 4.3 Маппинг контента в озеро
- **Channel** — `upsert(Channel, {platform, external_id}, {title, url, meta})`. platform: `vk` / `telegram_channel` / `tiktok` / `instagram` / `youtube`.
- **Content** — `upsert(Content, {channel_id, external_id}, {type, title, url, published_at, metrics(latest), raw})`. `metrics` = **последний** снапшот (last-write-wins).
- **content_metric** (НОВАЯ таблица) — `upsert(ContentMetric, {content_id, snapshot_date}, {views, reach, likes, comments, shares, saves, raw})`. Одна строка на контент/день = **тайм-серия** трендов. Это единственное место истории метрик.
- **Source** — только при атрибуции (UTM/start-link/referral). Код строится **через общий канонический нормализатор UTM** (см. 4.5).
- **events** — только **неизменяемые факты**: `content_publish` один раз, dedup_key `'<name>:content:{id}:publish'`, occurred_at=published_at, БЕЗ перештамповки метрик. `subscriber_id=NULL` для контент-событий (проверить, что дайджест/вьюхи не делают INNER JOIN на subscribers).

### 4.4 Идемпотентность и масштаб
- Всё через `kontur/db.upsert` (select-then-write) по существующим UniqueConstraints.
- **Глобальная сериализация инжеста** (один cron-слот): `upsert` не атомарен, а несколько коннекторов делят ключи `Source(kind,code)` / `Channel` → гонка. При росте — заменить запись общих ключей на `INSERT … ON CONFLICT` за той же сигнатурой.
- **Batch-commit для бэкфилла** (коммит每N/страница), а не одна транзакция на весь прогон: контент-бэкфилл — тысячи объектов; `raw_records` лендится первым → resume дёшев. Инкрементальные окна `--since/--full`.
- **Мутабельные метрики:** растут со временем. История — ТОЛЬКО в `content_metric` (dated) / `raw_records` (dated). `Content.metrics` — явно «последнее».
- **Soft-delete / seen_in_run:** удалённый/скрытый контент просто пропадает из листинга — коннектор не видит удаление, строки `Content` тихо устаревают. Помечать `last_seen_run_id`; если нужна churn-аналитика — мягкое удаление.
- **Таймзоны:** `_ts()` от legacy funnel platform = unix UTC; YouTube отдаёт ISO-строки, VK — unix (день по Москве). Каждый `mapping.py` парсит в tz-aware UTC, иначе `snapshot_date` уезжает на день у полуночи.

### 4.5 Сквозные общие узлы
- **Канонический нормализатор UTM** (один общий helper, не per-connector): единый словарь ключей (`utm_source`→`utmSource`), та же сортировка/регистр/формат `Source.code`. Контент-коннекторы и бот-источник воронки строят `code` через него, чтобы UTM из контента совпал с UTM, под которым подписчик пришёл в воронку. Тест: контент-Source и subscriber-Source из одного логического UTM дают идентичный `code`. Иначе атрибуция «контент→воронка» молча даёт ноль джойнов.
- **Хранилище OAuth refresh-токенов** (для YouTube/IG/TikTok-API): нынешний клиент держит токен только в памяти. Нужна однострочная таблица токен-стора (или файл) на РФ-БД; клиент читает/пишет её в `_token()/_force_refresh()`. Тест: «refresh-токен сохранён и переиспользован новым инстансом клиента».
- **Слой rate-limit/backoff в `client.py`** (legacy funnel platform не нужен был): VK error 6, YouTube квоты 10k юнитов/день, Telegram FLOOD_WAIT.
- **httpx: один injection point.** httpx молча игнорирует `proxy=`, если задан `transport=` (проверено на 0.28.1). НЕ выставлять оба параметра независимо: прод-прокси строится как `httpx.HTTPTransport(proxy=…)`, тесты — `MockTransport`. Тест проверяет, что прод-клиент реально несёт прокси (`client._transport`), а не только что запрос прошёл.

---

## 5. Коннекторы каналов (per-channel спеки)

### 5.1 VK — `kontur/connectors/vk/`
**Фичибилити:** официальный API. **Почва:** РФ-VPS напрямую, без релея (VK глушится вне РФ; даже наш билд/CI вне РФ не достучится до api.vk — фикстуры коммитим).

**Два токена (ключевая находка):** статистику (охваты/просмотры/визиты) community-токен НЕ отдаёт.
- `VK_COMMUNITY_TOKEN` — `wall.get` (посты, views/likes/reposts/comments), `groups.getById` (members_count, title);
- `VK_USER_STATS_TOKEN` — user-токен админа, scope `stats,offline`, для `stats.get` / `stats.getPostReach`.

**Данные:** Channel(platform='vk'), Content (посты), content_metric (per-post reach/views по дням), Source(kind='vk_community'), event `content_publish`.

**Подводные камни (от скептика):**
- **HIGH — домены переезжают на `.vk.ru`** (cutover ~30 сент): хардкод `api.vk.com/oauth.vk.com/dev.vk.com` — мина. → `VK_API_BASE`/`VK_OAUTH_BASE` конфигурируемы (дефолт `api.vk.ru`); токен минтить на том же домене, что и зовём; падать громко на 30x.
- **HIGH — stats-токен хрупкий** (Implicit Flow + offline идут под VK ID-деприкейшн): не вечный. → хранить дату выпуска; на error code 5 — громкое «переавторизуй VK stats токен», НЕ падать всем прогоном; stats-обогащение строго опционально (контент-синк работает на одном community-токене).
- **HIGH — мутабельные метрики:** `upsert` перетирает строку → «reach over time» жить негде. → история в `content_metric`/`raw_records` (dated), `content_publish` один раз без перештамповки.
- **MEDIUM — провизорные данные:** `stats.get` за свежие дни доревизуются вверх 1–3 дня. → дневной агрегат в `raw_records` (есть `updated_at`), перетираем пока день в окне ревизии.
- **MEDIUM — VARCHAR(500):** длинный первый «абзац» поста рвёт Postgres-транзакцию. → `title=first_line[:500]` (лучше 200+…), полный текст в `raw`; гард на url.
- **MEDIUM — `execute` не повышает 3 rps** и прячет ошибки в `execute_errors`. → при нашем объёме (десятки-сотни постов) — последовательно, ≥0.34с + backoff на code 6.
- **LOW — не гейтить на `members_count`** (бывает скрыт): просто пробовать `getPostReach`, деградировать на ошибке; базовый reach — `views.count` из `wall.get`.
- **LOW — IP-allowlist** к временному VPS — операционная ловушка (VPS выводим). → проверить, требуется ли вообще; если да — в runbook миграции.

### 5.2 Telegram-канал — `kontur/connectors/telegram_channel/`
**Фичибилити:** официально, но Bot API даёт ТОЛЬКО title + число подписчиков (нет per-post просмотров). Реальная статистика — **MTProto user-сессия (Telethon)**, аккаунт должен быть **админом** канала.

**Почва:** **через офшорный релей** (РФ душит Telegram). api_id/api_hash и первый логин — с офшорного/доверенного IP (обязательно).

**Креды:** `TELEGRAM_CHANNEL_ID` (-100…), `TG_API_ID`, `TG_API_HASH`, `TG_SESSION` (StringSession компанейского аккаунта — хранить как пароль), `TG_PHONE` (только бутстрап). Bot API-слой переиспользует существующий `TELEGRAM_BOT_TOKEN` (бот уже админ канала с деплоя воронки).

**Данные:** Channel(platform='telegram_channel', external_id='-100…' — НЕ путать с legacy_funnel-Channel platform='telegram'/referral; разные строки, не сливаются автоматически), Content (посты, `.views/.forwards` best-effort), content_metric (per-post views по дням; источник истины — `getMessageStats.views_graph` где `can_view_stats=true`), event роста подписчиков.

**Подводные камни (от скептика):**
- **CRITICAL — РФ блокирует Telegram** → релей/офшор-бутстрап (см. выше), бюджеты FLOOD_WAIT/timeout, алярм на деградацию.
- **HIGH — Telethon не установлен; Python 3.14.3**; 3.14-совместимость только с Telethon 1.44.0 (2026-06-15); cryptg/tgcrypto wheels под cp314 могут отсутствовать (иначе медленный pure-Python AES). → пин `telethon>=1.44.0`, проверить импорт+логин на 3.14 на самом VPS, добавить в pyproject как first-class dep.
- **HIGH — async vs sync:** Telethon только async, наш контур синхронный. → отдельный async CLI-энтрипоинт (`python -m kontur.cli telegram sync`, свой `asyncio.run`), DB-запись синхронно между await; НЕ хостить Telethon в живом aiogram-процессе бота.
- **HIGH — смерть сессии** (AuthKeyRevoked/SessionPasswordNeeded/флаг): громкий `error` + runbook ре-логина (автоматом не восстановить). Компанейский аккаунт → смерть не трогает владельца.
- **HIGH — dedup_key с дата-бакетом:** рост `'tg:subcount:<ch>:<YYYY-MM-DD>'`, reach `'tg:reach:<ch>:<msg_id>:<YYYY-MM-DD>'` — иначе одна точка навсегда или дубли.
- **MEDIUM — VARCHAR(500)** (как VK): сниппет в title, полный текст в raw.
- **MEDIUM — `.views` бывает None:** best-effort, не фабриковать нули; админский `getMessageStats/getBroadcastStats` — авторитетный источник.
- Использовать `client.get_stats()` (сам роутит на stats-DC и резолвит async-графы), не приватный `_borrow_exported_sender`.

### 5.3 TikTok — `kontur/connectors/tiktok/`
**Фичибилити:** официального API под органику НЕТ (Research API — только академики US/EU; Display API — публикация/публичный профиль; приватную аналитику не отдают). → **парсер выгрузок** TikTok Studio. **Почва:** не сетевой (файл).

**Как получаем данные:** владелец в TikTok Studio → Analytics → период → Export (CSV/XLSX). Глубина 60 дней (Business Hub — до года). **Нужен реальный образец файла** — привязать парсер к фактическим колонкам (RU/EN-локаль и csv/xlsx различаются). Креды не нужны.

**Данные:** Channel(platform='tiktok'), Content (видео: views/likes/comments/shares/watch_time), content_metric (по датам экспорта), event `content_publish`. Приём файла — в кабинет-бот (drag файл) или папку; raw-файл → `raw_records`.

**Подводные камни:**
- **CRITICAL — нет автоматизации:** владелец выгружает руками по расписанию. → максимально просто (файл в бот) + еженедельное напоминание.
- **HIGH — колонки/локаль плавают:** маппинг по имени заголовка + таблица синонимов + громкое падение на неизвестной схеме; пин к образцу; юнит-тест на реальном файле.
- **HIGH — нестабильный id видео:** если в файле нет — из URL; иначе hash(title+published_at); задокументировать.
- **MEDIUM — 60-дневное окно ретенции:** еженедельная каденция + Business Hub год для бэкфилла; каждый raw-файл в `raw_records` (идемпотентный ре-импорт более широкого диапазона безопасен — last-wins).
- Платные скрейперы (Apify/TokAPI) меняют легал-постуру + стоят → не в MVP.

### 5.4 Instagram — `kontur/connectors/instagram/`
**Фичибилити:** официальный Graph API (аккаунт Business/Creator подтверждён). **Почва:** **только офшор** — Meta запрещена («экстремистская» с 2022) и Instagram жёстко заблокирован в РФ (фев 2026); закон июля 2025 криминализует доступ к экстремист-материалам даже через VPN. Весь Meta-трафик + хранение токена/приложения — офшорно, отдельно от РФ-озера ПД.

**Креды:** `META_APP_ID`, `META_APP_SECRET`, `IG_USER_ID`, `FB_PAGE_ID`, `IG_LONG_LIVED_TOKEN` (60 дней, нужен refresh-job), клиент берёт `proxy_url` (офшорный релей).

**Данные:** Channel(platform='instagram'), Content (media: reach, impressions, saves, plays для reels), content_metric (по дням), account insights (follower_count) → event/raw.

**Подводные камни:**
- **CRITICAL (гео+легал):** РФ-egress заблокирован/нелегален → только офшорный релей, токен+приложение офшорно.
- **HIGH — App Review 4–6 недель**, могут отклонить → Instagram НЕ быстрый. → бумаги на ревью подаём рано параллельно; пока строим/тестируем на Dev-режиме + аккаунт владельца как tester (для своих данных часто работает без полного ревью — проверить).
- **HIGH — токен 60 дней:** unattended refresh-job обязателен (refresh после 24ч, до 60д) + алярм на сбой, иначе данные тихо встают.
- **MEDIUM — деприкейшн версий Graph API** (~ежегодно), переименования метрик (impressions→views). → пин версии в клиенте, карта имён метрик в `mapping.py`.
- **MEDIUM — retention-окна insights + rate-limits** → ежедневный pull, снапшоты, backoff.

### 5.5 YouTube — `kontur/connectors/youtube/` (позже)
**Фичибилити:** Data API v3 (публичные счётчики, ключ) vs Analytics API (watch time, traffic sources, retention — OAuth владельца). **Почва:** офшорный релей.

**Подводные камни:** OAuth refresh-токен надо персистить (см. 4.5; в режиме «Testing» refresh-токен мрёт за 7 дней → опубликовать consent в Production, либо жить на публичном Data API без Analytics). Квота 10k юнитов/день, `search.list`=100 юнитов → `playlistItems.list` (1 юнит) + `videos.list` батч до 50 id. Берём после быстрых каналов.

---

## 6. Фундамент C

### 6.1 Бот → события озера (прямая запись в БД)
Бот уже пишет `Payment` напрямую через SQLAlchemy на том же хосте (`bot/bot.py:288-307`). Вебхук-вариант лендит только `raw_records` (без Event-строк) + лишний HTTP-хоп → хуже. **Решение: прямая запись.**

Новый `kontur/ingest.py`: `record_funnel_event(tg_id, event_type, *, tariff=None, step_index=None, occurred_at=None, raw=None)` — апсертит Subscriber (`source_system='telegram_bot'`, external_id=str(tg_id), tg_user_id=str(tg_id)) + Event по `(source_system, dedup_key)`.

| Триггер (бот) | event_type | stage | dedup_key |
|---|---|---|---|
| `cmd_start` | `bot_start` | welcome | `tg{id}:bot_start` (первый контакт) |
| `send_step` 1 | `step_enter` | package_choice | `tg{id}:step:1` |
| `send_step` 2/3/4 | `step_enter` | package_info(+tariff) | `tg{id}:step:{i}` |
| `on_button` pay | `checkout` | checkout(+tariff) | `tg{id}:checkout:{tariff}` |
| `on_paid` | `payment` | paid(+tariff) | `tg{id}:payment:{order_id}` |

`order_id` (= `Payment.external_id`) даёт общую идентичность Event и Payment. **legacy funnel platform больше не источник:** воронка полностью на нашем коде, `legacy_funnel sync` не запускаем и коннектор не расширяем. Бот — единственный источник событий воронки. Исторические строки legacy funnel platform, уже импортированные в Phase 1 (если они есть в озере), остаются как есть; новые события пишет только бот (`source_system='telegram_bot'`).

- **MEDIUM — блокирующий DB I/O в aiogram-петле:** оборачивать записи в `asyncio.to_thread`, best-effort try/except — воронка и 200-ответ Prodamus не должны блокироваться записью в озеро.
- **LOW — `upsert` не атомарен:** ловить IntegrityError как «уже записано».
- Единый module-level engine в `ingest.py` (а не `make_engine` на каждый вызов).

### 6.2 Релей LLM — доведение
WIP уже когерентен (5 файлов: `config.py` `llm_proxy_url`; `ai/llm.py` `DefaultHttpxClient(proxy=…)` — сквозной TLS/CONNECT, релей видит шифротекст; `cli.py` `_make_llm`; `.env.example`; `docker-compose.yml`). **Закоммитить как ОДИН коммит** (deploy-hygiene: не пунтить). Кода больше не требуется.

`LLM_PROXY_URL=http://user:pass@RELAY_IP:3128`; пусто = напрямую. Релей (squid, офшор) — allowlist egress только на `api.anthropic.com`.

**Рецепт проверки на РФ-VPS:**
1. `curl -sS -o /dev/null -w '%{http_code}' -x "$LLM_PROXY_URL" https://api.anthropic.com/v1/models -H "x-api-key: $LLM_API_KEY" -H "anthropic-version: 2023-06-01"` → ждём `200`. Тот же curl БЕЗ `-x` должен виснуть (доказывает, что блок реален и чинит именно прокси).
2. `python -c "AnthropicLLM(key, proxy_url=…).complete(...)"` — реальный SDK-путь.
3. `python -m kontur.cli ai ask "…" --show-prompt` (сухо, без ключа) → затем реально с ключом+прокси.

### 6.3 Два бота — кабинет аналитики
Оба бота на одном VPS, общий `database_url`. Воронка пишет (events/payments); кабинет — read-mostly, пишет только `ai_reports` при разборе. Postgres держит конкуренцию, схему не делим.

**Кабинет читает:** вьюхи `kontur/dashboard/views.py` (`v_kpis, v_funnel, v_revenue_by_tariff, v_revenue_by_source, v_subscribers, v_payments`) + таблицу `ai_reports`; новый разбор — `analyst.answer_question(factory, llm, question)`.

**Структура:** новый пакет `cabinet/` параллельно `bot/`: `cabinet/bot.py` (энтрипоинт `python -m cabinet.bot`, свой `CABINET_BOT_TOKEN`, systemd `kontur-cabinet`, allowlist `CABINET_OWNER_IDS` на каждом хендлере), `cabinet/queries.py`, `cabinet/format.py`. Переиспользует `kontur/`.

**Общий сетевой слой:** вынести IPv4-пин воронки (`_PinnedResolver/_make_session/_polling_forever`, `bot/bot.py:246-348`) в `bot/net.py` — кабинет упрётся в тот же RF-hosting `get_me` IPv6-таймаут.

**Поведение (решение №4):** кабинет показывает все метрики (KPI, воронка, выручка, метрики каналов; доступен фильтр по тарифным тегам через `parse_payment_tag`: `купил_премиум→premium`). Разбор модели — **по расписанию раз в неделю** (пуш владельцу) + по команде `/разбор`. Обычные запросы — цифры из вьюх, без трат токенов.
- **HIGH — кабинет показывает бизнес-данные + тратит токены:** `CABINET_OWNER_IDS` allowlist в начале каждого хендлера; всех прочих игнор.
- **MEDIUM — тот же IPv6-таймаут** → общий `bot/net.py`.
- **MEDIUM — релей = SPOF для разборов** (воронка не зависит): проверить релей до опоры; `FakeLLM/--show-prompt` как fallback.

---

## 7. Изменения схемы данных

- **Новая таблица `content_metric`**: `id, content_id (FK), snapshot_date (date), views, reach, likes, comments, shares, saves, raw (json), created_at, updated_at`, UNIQUE(content_id, snapshot_date).
- **`content`**: добавить `last_seen_run_id` (soft-delete сигнал); рассмотреть `title` → TEXT (или жёсткое усечение в mapping).
- **Токен-стор** для OAuth refresh (YouTube/IG): одна таблица `oauth_tokens(connector, access_token, refresh_token, expires_at, updated_at)` на РФ-БД (решить размещение под 152-ФЗ).
- **`config.Settings` + `.env.example`**: `VK_COMMUNITY_TOKEN, VK_USER_STATS_TOKEN, VK_GROUP_ID, VK_API_BASE, VK_OAUTH_BASE`; `TELEGRAM_CHANNEL_ID, TG_API_ID, TG_API_HASH, TG_SESSION`; `META_APP_ID, META_APP_SECRET, IG_USER_ID, FB_PAGE_ID, IG_LONG_LIVED_TOKEN`; `YT_*`; `CABINET_BOT_TOKEN, CABINET_OWNER_IDS`; per-connector `*_PROXY_URL` (или общий relay).
- **Миграции:** проект на `db/schema.sql` + `create_all`; перед прод-пушем — проверка прод-состояния (deploy-hygiene). Перегенерировать `db/schema.sql` из моделей.

---

## 8. Тестирование

- `mapping.py` каждого коннектора — чистые юнит-тесты на **канонических JSON/файловых фикстурах** (VK/IG/YT недостижимы из CI вне РФ/без офшора → фикстуры снимаем один раз с сервера и коммитим).
- `client.py` — httpx `MockTransport`: переиспользование токена, refresh после истечения, пагинация, обработка ошибок (VK — HTTP 200 + error body; не `raise_for_status`).
- Тест «прод-клиент реально несёт прокси» (`client._transport`).
- Тест канонической UTM-нормализации (контент-Source == subscriber-Source).
- `sync.py` — FakeClient + SQLite, идемпотентность (повторный прогон не плодит дублей), batch-commit resume.
- Живые smoke-тесты — за env-флагом, только на целевой почве (VK на РФ-VPS, TG/IG на офшоре).

---

## 9. Порядок работ (всё параллельно, с учётом внешних стен)

Раздаём по агентам (worktree-изоляция при параллельной правке):
1. **Фундамент-контракт** (base.py ABC + content_metric + UTM-нормализатор + токен-стор + httpx-injection) — разблокирует всех; делаем первым из общего, дальше каналы цепляются.
2. **C-фундамент:** бот→события (`ingest.py`), коммит релея, кабинет-бот (`cabinet/` + `bot/net.py`).
3. **VK** (РФ-VPS, быстро) ∥ **TikTok-парсер** (нужен образец файла) ∥ **Telegram-канал** (Telethon, офшор, async-энтрипоинт).
4. **Instagram** — App Review-бумаги стартуют **сразу**; код на Dev-режиме параллельно; прод-доступ догоняет.
5. **YouTube** — после, отдельным циклом.

Каждый блок перед реализацией снова проходит агента-скептика на подводные камни (как договорились).

---

## 10. Что нужно от владельца (доступы — научу доставать пошагово)

Полные click-by-click инструкции по каждому ключу выдам отдельным приложением в момент старта соответствующего коннектора (детальные шаги по VK и Telegram уже собраны ресёрчем). Кратко, что собрать:
- **VK:** community-токен (Управление → Работа с API → Ключи доступа) + user-stats-токен (свой Standalone-app на dev.vk.ru → Implicit Flow scope=stats,offline) + numeric group_id.
- **Telegram:** завести компанейский аккаунт (отдельный номер), сделать его админом канала; api_id/api_hash с my.telegram.org (логин с офшорного/доверенного IP); один раз интерактивный Telethon-логин → StringSession.
- **TikTok:** образец выгрузки TikTok Studio (CSV/XLSX) — разблокирует парсер.
- **Instagram:** аккаунт Business/Creator привязан к FB-странице; мы заводим Meta-app; владелец-админ авторизует scope insights; запускаем App Review рано.
- **Anthropic:** ключ модели (позже) + параметры офшорного релея (squid auth, IP).

**Операционные вопросы (не блокируют старт):** TG user-id владельца для `CABINET_OWNER_IDS`; подтверждение, что «по тегу» = тарифные сегменты (иначе дописать запись тегов в живую воронку).

---

## 11. Риски

- **Внешние стены доступа — график, не код:** App Review Meta (4–6 нед, могут отклонить), смена доменов VK (.vk.ru cutover), усиление блокировок Telegram/VPN в РФ. MVP твёрдо коммитит каналы с подтверждённым доступом (VK, TikTok-парсер); IG/YouTube — условно/догоняют.
- **Офшорный релей — SPOF** для TG/IG/LLM. Воронка и VK от него не зависят. Проверять перед опорой; алярмы на деградацию.
- **Атрибуция контент→воронка не доказана**, пока UTM-конвенции не сверены с реальными ссылками владельца. До сверки атрибуция — гипотеза.
- **Временный РФ-VPS выводится** (план хостинга): IP-allowlist VK и TG-сессия привязаны к хосту → runbook миграции.
