# Воспроизводимый deploy

Боевой API, CLI-коннекторы и Telegram-бот собираются из одного `Dockerfile` и одного
dependency lock. Compose запускает два процесса из одного versioned image:

- `app` — FastAPI и CLI;
- `bot` — aiogram polling + Prodamus webhook на `127.0.0.1:8081`.

## Подготовка VPS

Рабочий каталог должен быть checkout конкретного коммита `main`. Секреты и клиентские
данные остаются вне Git:

- `/opt/kontur/.env`, права `0600`;
- `/opt/kontur/raw/bothelp_raw.json`;
- дополнительные файлы в `/opt/kontur/media/`.

`COMPOSE_PROJECT_NAME=kontur` и текущий `KONTUR_IMAGE` сохраняются в prod `.env`
самим deploy-скриптом.

## Deploy

```bash
cd /opt/kontur
git fetch origin
git switch main
git pull --ff-only origin main
sudo ./ops/deploy.sh
```

Скрипт:

1. отказывается деплоить незакоммиченные tracked-файлы;
2. проверяет наличие `.env` и сырья бота;
3. запускает проверенный backup;
4. собирает `kontur-app:<git sha>` из pinned base image и `requirements.lock`;
   если registry временно отвечает rate-limit, пересобирает тот же lock и код поверх
   последнего проверенного app image;
5. сохраняет прежний app image как `kontur-app:rollback-<timestamp>`;
6. обновляет API и бота;
7. ждёт Docker healthchecks и проверяет оба локальных endpoint;
8. при первой миграции отключает старый `kontur-bot.service` только после успешного запуска;
9. при любой ошибке автоматически возвращает прежний API и systemd/Compose-бот.

## Проверка

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8081/health
curl -I https://slapychev.ru/prodamus  # GET должен вернуть 405
systemctl is-active kontur-backup.timer nginx docker
```

Дополнительно проверить одну карточку Metabase и отсутствие новых ошибок:

```bash
docker logs --since 10m kontur-app-1
docker logs --since 10m kontur-bot-1
```

## Ручной rollback

Для будущих версий оба сервиса можно вернуть на сохранённый unified image:

```bash
# В .env заменить KONTUR_IMAGE на нужный kontur-app:rollback-... или kontur-app:<sha>
docker compose up -d --no-deps app bot
```

При rollback на образ, созданный до контейнеризации бота, остановить Compose `bot` и
временно вернуть `kontur-bot.service`.
