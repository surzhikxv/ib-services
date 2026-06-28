# Instagram: как выдать доступ к аналитике (для владельца аккаунта)

Нужен ОДИН секрет — 60-дневный токен. Путь B (Instagram API with Instagram Login):
без привязки к Facebook-странице и без проверки приложения (свой аккаунт).

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

## Запуск
- Разовый бэкафилл (сразу после получения токена — окно 90 дней невосстановимо):
  `python -m kontur.cli instagram backfill`
- Дневной синк (cron): `python -m kontur.cli instagram sync --demographics`
- Продление токена (cron, напр. еженедельно): `python -m kontur.cli instagram refresh-token`
