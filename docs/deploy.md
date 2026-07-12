# Воспроизводимый deploy

Боевой API, CLI-коннекторы и Telegram-бот собираются GitHub Actions из одного
`Dockerfile` и одного dependency lock. После тестов workflow публикует image в GHCR
с двумя тегами:

- `ghcr.io/surzhikxv/ib-services:<полный git sha>` — immutable release;
- `ghcr.io/surzhikxv/ib-services:main` — только удобный указатель на последний build.

Deploy использует SHA-тег, проверяет OCI revision и сохраняет в `.env` уже resolved
digest вида `ghcr.io/surzhikxv/ib-services@sha256:...`. Compose запускает два процесса
из одного digest:

- `app` — FastAPI и CLI;
- `bot` — aiogram polling + Prodamus webhook на `127.0.0.1:8081`.

## Подготовка VPS

Рабочий каталог должен быть checkout конкретного коммита `main`. Секреты и клиентские
данные остаются вне Git:

- `/opt/kontur/.env`, права `0600`;
- `/opt/kontur/bot/funnel.json` входит в Git и поставляется внутри image;
- дополнительные файлы в `/opt/kontur/media/`.

`COMPOSE_PROJECT_NAME=kontur` и текущий `KONTUR_IMAGE` сохраняются в prod `.env`
самим deploy-скриптом.

## Deploy

Сначала убедиться, что workflow **Test and publish app image** для нужного коммита
завершился успешно. Затем:

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
3. скачивает из GHCR образ с полным SHA текущего checkout;
4. до backup и перезапуска проверяет OCI-label `org.opencontainers.image.revision`;
5. разрешает tag в immutable registry digest;
6. запускает проверенный backup и сохраняет прежний image как
   `kontur-app:rollback-<timestamp>`;
7. обновляет API и бота;
8. ждёт Docker healthchecks и проверяет оба локальных endpoint;
9. при первой миграции отключает старый `kontur-bot.service` только после успешного запуска;
10. при любой ошибке автоматически возвращает прежний API и systemd/Compose-бот.

Если GHCR image ещё не собран, скрипт завершится **до** backup и изменения сервисов.
Production не должен собирать обычные релизы самостоятельно.

### Аварийная локальная сборка

Только если GitHub/GHCR недоступен и исправление нельзя отложить:

```bash
sudo KONTUR_ALLOW_LOCAL_BUILD=1 ./ops/deploy.sh
```

В этом режиме остаётся прежний fallback поверх последнего проверенного production image.
После восстановления GHCR следует повторить обычный deploy, чтобы вернуться на digest.

## Проверка

```bash
docker compose ps
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8081/health
curl -s -o /dev/null -w '%{http_code}\n' https://slapychev.ru/prodamus  # 403 без подписи
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
# В .env заменить KONTUR_IMAGE на нужный kontur-app:rollback-... или GHCR digest
docker compose up -d --no-deps app bot
```

При rollback на образ, созданный до контейнеризации бота, остановить Compose `bot` и
временно вернуть `kontur-bot.service`.
