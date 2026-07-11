# Контур роста

Production-система для курса Сергея Лапычева: собственная Telegram-воронка,
озеро данных, коннекторы соцсетей, Metabase и ИИ-аналитика.

Стек: Python 3.12 · aiogram · FastAPI · PostgreSQL · n8n · Metabase · Docker Compose.

## Что работает

- Собственный Telegram-бот: `/start` → знакомство → выбор тарифа → Prodamus → доступ в канал.
- События воронки, подписчики и оплаты напрямую пишутся в PostgreSQL.
- Напоминания неоплатившим каждые 48 часов.
- Коннекторы Telegram-канала, VK, YouTube и ручной импорт TikTok.
- Планировщик и контроль свежести через `kontur-sync.timer` и `/health/connectors`.
- 11 карточек Metabase: KPI, воронка, деньги, динамика и состояние источников.
- Единый versioned image в GHCR, deploy по immutable digest, backup и rollback.

Instagram и ИИ-отчёты требуют внешних токенов владельца; код интеграций готов.

## Структура

```text
bot/
  funnel.json       versioned source of truth: тексты, кнопки и явные маршруты
  bot.py            aiogram polling, воронка и post-payment UX
  payments.py       ссылки и HMAC-проверка Prodamus
  webhook.py        приём подтверждённых оплат
  channel.py        персональные инвайты и join requests
  reminders.py      напоминания неоплатившим
  content.py        строгая загрузка funnel snapshot v1
  routing.py        валидация явных маршрутов
  media.py          локальные runtime-медиа
kontur/
  models.py         схема озера данных
  ingest.py         события собственного Telegram-бота
  automation.py     расписание и freshness monitoring
  connectors/       Telegram-канал, VK, YouTube, TikTok, Instagram
  dashboard/        SQL-вьюхи, каталог и provision Metabase
  ai/               дайджест, промпты и LLM-аналитик
ops/
  deploy.sh         GHCR deploy + healthchecks + rollback
  backup.sh         проверяемые backup PostgreSQL/Metabase/n8n
  kontur-sync.*     systemd service/timer коннекторов
```

## Локальный запуск

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.lock
./.venv/bin/pip install --no-deps -e .
./.venv/bin/pip install pytest==9.1.0
cp .env.example .env

./.venv/bin/python -m pytest -q
./.venv/bin/python -m bot.preview
./.venv/bin/python -m kontur.cli automation status
```

Без `DATABASE_URL` используется `data/kontur.sqlite`.

Запуск собственного бота:

```bash
export TELEGRAM_BOT_TOKEN=...
python -m bot.bot
```

## Docker

```bash
docker build --build-arg VCS_REF=local -t kontur-app:latest .
docker compose up -d
docker compose run --rm app python -m kontur.cli automation status
```

- API: `http://localhost:8000/health`
- Свежесть источников: `http://localhost:8000/health/connectors`
- Prodamus webhook: `http://localhost:8081/prodamus`
- n8n: `http://localhost:5678`
- Metabase: `http://localhost:3000`

## Данные

Путь человека «контент → Telegram-бот → тариф → оплата» хранится в единой модели:

- `subscribers`, `events`, `payments` — воронка и деньги;
- `channels`, `content`, `content_metrics`, `channel_metrics` — соцсети;
- `sources` — deep-link/UTM атрибуция;
- `sync_runs` — состояние коннекторов;
- `ai_reports` — сохранённые аналитические разборы.

DDL генерируется из `kontur/models.py`:

```bash
python -m kontur.cli db schema --dialect postgresql
```

## Документация

- [Telegram-воронка и Prodamus](docs/bot.md)
- [Deploy и rollback](docs/deploy.md)
- [Backups, безопасность и синхронизация](docs/operations.md)
- [Metabase](docs/metabase.md)
- [ИИ-аналитик](docs/ai-analyst.md)
- [YouTube OAuth](docs/connectors/yt-oauth-bootstrap.py)
- [VK access](docs/connectors/vk-access.md)
- [TikTok import](docs/connectors/tiktok-access.md)
- [Instagram access](docs/instagram-token-runbook.md)
