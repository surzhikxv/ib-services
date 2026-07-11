# Эксплуатация и восстановление

## Резервные копии

`kontur-backup.timer` запускает `/opt/kontur/ops/backup.sh` каждую ночь в
`00:30 UTC` с небольшим случайным сдвигом. Копии хранятся 30 дней в
`/var/backups/kontur/<UTC timestamp>/`:

- `postgres.dump` — custom-format dump основной PostgreSQL БД;
- `metabase-data.tar.gz` — консистентная копия application DB Metabase;
- `n8n-data.tar.gz` — консистентная копия состояния n8n;
- `SHA256SUMS` — контрольные суммы всех файлов.

Metabase и n8n кратко останавливаются на время копирования их файловых БД и
автоматически запускаются даже при ошибке backup. PostgreSQL копируется online.

Проверка и ручной запуск:

```bash
systemctl list-timers kontur-backup.timer --all
systemctl start kontur-backup.service
journalctl -u kontur-backup.service -n 100 --no-pager
```

Копии на том же VPS защищают от ошибки приложения/volume, но не от потери всего
сервера. Следующий обязательный шаг — репликация каталога в off-site storage или
snapshot-диска у VPS-провайдера.

## Восстановление

Восстановление меняет рабочие данные и выполняется только в maintenance window.
Перед ним нужно остановить `kontur-app-1`, `kontur-bot.service`, Metabase и n8n.

Проверить PostgreSQL dump без восстановления:

```bash
docker exec -i kontur-postgres-1 pg_restore --list < /var/backups/kontur/<stamp>/postgres.dump
```

Архивы файловых БД предварительно проверяются через `tar -tzf` и `sha256sum -c`.
После восстановления обязательно проверить `/health`, Prodamus route, все карточки
Metabase и статус `kontur-bot.service`.

## Разделение прав

Metabase подключается к основной БД ролью `metabase_ro`. У неё нет
`SUPERUSER/CREATEDB/CREATEROLE`, включён `default_transaction_read_only`, выдан только
`SELECT` на текущие и будущие таблицы/views схемы `public`.

Административные креды Metabase и пароль `metabase_ro` хранятся только в prod `.env`
с правами `0600`; в репозиторий значения не попадают.

## Публичные endpoints

- `/docs`, `/redoc` и `/openapi.json` закрыты на публичном nginx;
- `/webhooks/{source}` скрыт из OpenAPI, ограничен nginx и требует отдельный
  `X-Kontur-Token`; при пустом `WEBHOOK_INGEST_TOKEN` endpoint выключен;
- Prodamus не использует общий endpoint — его `/prodamus` проверяет HMAC отдельно;
- Metabase и n8n защищены своими логинами, nginx rate/connection limits и security headers.

Версии PostgreSQL, n8n и Metabase закреплены digest-ами в `docker-compose.yml`.
Обновлять их следует явно: backup → pull нового digest → smoke-test → замена pin.

## Синхронизация и свежесть источников

`kontur-sync.timer` просыпается дважды в сутки, около `03:20` и `15:20 UTC`
(плюс случайный сдвиг до 10 минут). Один запуск:

- обновляет Telegram-канал, если последнему успеху не меньше 10 часов;
- обновляет VK и YouTube, если последнему успеху не меньше 20 часов;
- повторяет временно упавший коннектор до трёх раз, не блокируя остальные;
- контролирует ручной импорт TikTok, но не запускает его без свежего browser export;
- пишет полную сводку в journald;
- при заданном `SYNC_ALERT_CHAT_ID` отправляет проблемы в Telegram через текущего бота.

Проверка и ручной запуск:

```bash
systemctl list-timers kontur-sync.timer --all
systemctl start kontur-sync.service
journalctl -u kontur-sync.service -n 150 --no-pager
docker compose run --rm --no-deps app python -m kontur.cli automation status
curl -fsS http://127.0.0.1:8000/health/connectors
```

Порог застоя: 18 часов для Telegram, 30 часов для VK/YouTube и 8 дней для
ручного TikTok. Ошибка одного источника не отменяет попытки обновить остальные.
