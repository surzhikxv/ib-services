# Контур роста

MVP системы аналитики и ИИ-курирования для инфобизнеса: **озеро данных + ИИ над ним**.
Собираем данные всех каналов в одну базу → строим воронку от видео до оплаты → ИИ даёт
разборы и рекомендации. Полный бриф — в [HANDOFF.md](HANDOFF.md).

Стек: Python / FastAPI · PostgreSQL · n8n (оркестрация) · Metabase (дашборд) · LLM по API.

## Статус (Phase 1, пункты 1–2 — готово)

- ✅ **Фундамент**: репозиторий, `docker-compose` (Postgres + n8n + Metabase + app), схема озера данных.
- ✅ **Коннектор BotHelp на живых данных**: OAuth с авто-обновлением токена, выгрузка ботов,
  подписчиков (курсорная пагинация), 28 шагов воронки; маппинг шагов в этапы; распознавание
  тарифов и оплат по тегам; запись в озеро; CLI запуска. Идемпотентно (можно по расписанию).
- 🟡 Каркасы: приём вебхуков (живые события), базовый класс коннектора для остальных источников.

Дальше (не в этой сессии): остальные коннекторы, дашборд-панели, ИИ-разборы, продакшн-деплой.

## Структура

```
kontur/
  config.py                  настройки из .env
  db.py                      движок, init схемы, сиды справочников, портируемый upsert
  models.py                  СХЕМА ОЗЕРА (источник истины): каналы, контент, источники,
                             подписчики, тарифы, этапы/шаги воронки, события, оплаты, raw
  webhooks.py                приём живых событий в сырое озеро (каркас)
  api.py                     FastAPI: /health, POST /webhooks/{source}
  cli.py                     CLI: db init | db schema | bothelp sync
  connectors/
    base.py                  базовый класс коннектора (каркас)
    bothelp/
      client.py              HTTP-клиент: OAuth + авто-рефреш токена + пагинация
      mapping.py             ЧИСТАЯ логика: шаг→этап, тег→оплата/тариф, единая модель событий
      sync.py                оркестрация выгрузки → озеро
db/schema.sql                DDL озера для Postgres (сгенерирован из models.py)
tests/                       pytest (маппинг, клиент, синк, вебхуки) — 59 тестов
docker-compose.yml           Postgres + n8n + Metabase + app
```

## Запуск локально (без Docker)

Проверено на macOS, Python 3.14. Сетевой слой на `httpx` (тащит certifi и поддержку SOCKS),
поэтому не спотыкается о баг с сертификатами в python.org-сборках.

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"

cp .env.example .env          # и заполнить BOTHELP_* (или они уже есть)

./.venv/bin/python -m pytest                      # тесты
./.venv/bin/python -m kontur.cli bothelp sync     # ВЫГРУЗКА BotHelp на живых данных
```

Без `DATABASE_URL` данные пишутся в `data/kontur.sqlite` — удобно для проверки.
`bothelp sync` сам создаёт схему и сиды, повторный запуск не плодит дублей.

## Запуск через Docker (боевой контур)

```bash
docker compose up -d
docker compose run --rm app python -m kontur.cli bothelp sync
```

- API: http://localhost:8000/health, вебхуки — `POST /webhooks/bothelp`
- n8n: http://localhost:5678 · Metabase: http://localhost:3000 (подключить как источник
  наш Postgres: host `postgres`, БД из `.env`)

## Схема озера данных

Единая событийная модель: всё, что происходит с человеком по пути «контент → бот → оплата»,
ложится в `events` (тип, время, этап/шаг воронки, тариф, источник). Нормализованные сущности —
`channels / content / sources / subscribers / tariffs / funnel_stages / funnel_steps / payments`,
сырьё коннекторов — `raw_records`, журнал выгрузок — `sync_runs`.

DDL: [`db/schema.sql`](db/schema.sql). Перегенерировать: `python -m kontur.cli db schema > db/schema.sql`.

## BotHelp: как устроена выгрузка

- Аналитику воронки BotHelp по API не отдаёт — **строим её у себя** из подписчиков/тегов.
- **Тарифы и оплаты распознаём по тегам** вида `купил_базовый` / `купил_стандарт` / `купил_премиум_`
  (один подписчик может купить несколько тарифов → несколько оплат).
- 28 шагов бота «Курс» маппятся в этапы: `welcome → package_choice → package_info → checkout
  (премиум/базовый/стандарт) → paid → churn`, служебные (`Действия/Сообщение/Задержка`) → `service`.
- Точное время и сумму оплаты добираем **вебхуком** (Prodamus внутри BotHelp) — каркас готов.

## Безопасность

Креды BotHelp — только в локальном `.env` (в git не попадает). Перевыпуск:
BotHelp → Настройки → Интеграции → Open API → «ОБНОВИТЬ».
