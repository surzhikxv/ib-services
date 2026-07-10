# Instagram: как выдать доступ к аналитике

Для проекта Сергея предпочтителен **Facebook Login / Page-linked** путь: если
Instagram Business/Creator привязан к Facebook Page, через `graph.facebook.com`
доступно больше поверхностей (Page → Instagram Business Account, media, insights,
comments/replies, активные Stories).

## Вариант A — Facebook Page-linked (рекомендуемый для максимального сбора)

Нужны:
- Instagram-аккаунт в типе **Business** или **Creator**.
- Привязка Instagram к Facebook Page.
- Facebook User access token с доступом к этой Page и правами Instagram Graph.
- `INSTAGRAM_PAGE_ID` или `FB_PAGE_ID` этой Facebook Page.

`.env`:

```dotenv
INSTAGRAM_AUTH_MODE=facebook
INSTAGRAM_ACCESS_TOKEN=<facebook-long-lived-user-token>
INSTAGRAM_PAGE_ID=<facebook-page-id>
INSTAGRAM_API_VERSION=v25.0
INSTAGRAM_TIMEZONE=Europe/Moscow
IG_PROXY_URL=<relay-if-needed>
```

`INSTAGRAM_USER_ID` можно не задавать: коннектор сам вызовет
`/{page_id}?fields=instagram_business_account{...}` и получит ID Instagram
Business Account. Если ID уже известен, можно задать `INSTAGRAM_USER_ID` и
пропустить резолв через Page.

Запуск максимального дневного сбора:

```bash
python -m kontur.cli instagram sync --demographics --stories --comments
```

Для бэкафилла:

```bash
python -m kontur.cli instagram backfill --days 90 --comments
```

`--stories` имеет смысл только для активных Stories: окно жизни около 24 часов,
поэтому их нужно снимать частым cron/n8n job. Comments/replies лендятся в
`raw_records` без отдельной миграции.

Важно: `python -m kontur.cli instagram refresh-token` продлевает только Instagram
Login token. Facebook Login token нужно обновлять через Meta/Facebook OAuth flow.

## Вариант B — Instagram Login (быстрый fallback)

Нужен один 60-дневный токен. Этот путь проще: `graph.instagram.com`, без привязки
к Facebook Page и без проверки приложения для своего аккаунта.

1. В приложении Instagram: Настройки → Тип аккаунта → переключить на
   **Business** или **Creator** (если ещё не).
2. Зайти на https://developers.facebook.com/apps → **Create App** → тип **Business**.
3. В приложении: добавить продукт **Instagram** → раздел
   «Set up Instagram business login» (API setup with Instagram login).
4. Добавить свой Instagram-аккаунт в приложение (Standard Access — проверка
   приложения НЕ требуется для своего аккаунта).
5. Нажать **Generate token** напротив аккаунта → войти в Instagram → скопировать
   **долгосрочный токен (60 дней)**.
6. Передать разработчику **только этот токен** (ID аккаунта определим сами через `/me`).

Дальше токен кладётся в `INSTAGRAM_ACCESS_TOKEN` (.env, bootstrap), сохраняется в
`oauth_tokens` и продлевается автоматически по cron
(`python -m kontur.cli instagram refresh-token`). Если 60 дней без продления —
токен умирает безвозвратно, нужно повторить шаги 5–6.

`.env`:

```dotenv
INSTAGRAM_AUTH_MODE=instagram
INSTAGRAM_ACCESS_TOKEN=<instagram-long-lived-token>
INSTAGRAM_API_VERSION=v25.0
INSTAGRAM_TIMEZONE=Europe/Moscow
IG_PROXY_URL=<relay-if-needed>
```

## Команды
- Разовый бэкафилл (сразу после получения токена — окно 90 дней невосстановимо):
  `python -m kontur.cli instagram backfill`
- Дневной синк (cron): `python -m kontur.cli instagram sync --demographics`
- Продление токена (cron, напр. еженедельно): `python -m kontur.cli instagram refresh-token`
