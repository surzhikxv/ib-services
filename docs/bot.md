# Собственная Telegram-воронка

Пакет `bot/` — production aiogram-бот. После `/start` пользователь проходит
знакомство, выбирает тариф, оплачивает через Prodamus и получает доступ в канал.

## Источник контента

Единственный источник правды — versioned snapshot [`bot/funnel.json`](../bot/funnel.json).
Он входит в Git и Docker image и содержит 28 шагов:

- 11 контентных шагов;
- 17 служебных шагов, оставленных для стабильности индексов;
- 13 переходов между шагами;
- 3 платёжных маршрута;
- 3 ссылки на каналы;
- 3 terminal-маршрута.

`bot/content.py` строго проверяет версию и последовательность индексов.
`bot/routing.py` валидирует каждый target до запуска polling.

Оффлайн-проверка без Telegram-токена:

```bash
python -m bot.preview
```

## Граф

```text
/start → [0] Приветствие
[0] «Мне интересно»  → [7] видео-приветствие
[7] «Продолжить»     → [1] Выбор пакета       «Назад» → [0]
[1] «Базовый»        → [2] Инфо базовый
    «Стандарт +»     → [3] Инфо стандарт
    «Премиум»        → [4] Инфо премиум       «Назад» → [7]
[2/3/4] «Оплата»     → персональная ссылка Prodamus
после оплаты         → экран доступа в канал + «Назад»
```

## Runtime-медиа

Медиа хранятся локально и поставляются вместе с image:

- `media/intro_note.mp4` — круговое видео шага 1;
- `media/welcome.mp4` — видео-приветствие шага 7.

Каталог можно переопределить через `BOT_MEDIA_DIR`, отдельные файлы — через
`INTRO_NOTE_PATH` и `WELCOME_VIDEO_PATH`.

## Чистый чат

При переходе бот удаляет сообщения предыдущего шага и показывает новый. Страницы
подтверждённой оплаты сохраняются, чтобы пользователь не потерял доступ. Служебные
`/all` и `/step N` ничего не удаляют.

## События и атрибуция

Бот напрямую записывает в озеро:

- `bot_start`;
- `step_enter`;
- `checkout`;
- `applied`;
- `payment`;
- `course_reminder`.

Источник — `telegram_bot`. Deep-link payload из `/start` превращается в
`Source(kind="start_link")`; поддерживаются UTM-алиасы `s/m/c/ct/t`. Вход без
payload получает источник `direct`. Источник подписчика наследуют все дальнейшие
события и оплаты.

## Напоминания

Пользователь без успешной оплаты получает один из трёх шаблонов раз в 48 часов.
Настройки:

```dotenv
BOT_REMINDERS_ENABLED=1
BOT_REMINDER_INTERVAL_HOURS=48
BOT_REMINDER_POLL_SECONDS=300
BOT_REMINDER_BATCH_SIZE=100
```

Успешная отправка фиксируется как `course_reminder`. Заблокировавшие бота пользователи
помечаются неподписанными и исключаются из следующих рассылок.

## Prodamus

```dotenv
PRODAMUS_DOMAIN=<shop>.payform.ru
PRODAMUS_SECRET=...
PUBLIC_BASE_URL=https://slapychev.ru
PRODAMUS_WEBHOOK_PORT=8081
PAYMENT_RETURN_URL=https://t.me/<bot>
PRODAMUS_SIGN_LINKS=1
```

Кнопка оплаты ведёт через подписанный `GET /prodamus?...`: бот фиксирует этап
`checkout` и без дополнительного клика перенаправляет человека в Prodamus. Платёжная
ссылка получает персональный `order_id` с `tg_id` и тарифом. Prodamus отправляет
`POST /prodamus`; бот проверяет HMAC-подпись, сохраняет оплату до Telegram-delivery и
быстро подтверждает webhook.

Исторические `subscribed_at`, источники и checkout после обновления восстанавливаются
идемпотентной командой `kontur db repair-funnel`.

## Доступ в канал

```dotenv
CHANNEL_BASIC_ID=
CHANNEL_STANDARD_ID=
CHANNEL_PREMIUM_ID=
```

Если бот администратор канала, после оплаты он создаёт персональный одноразовый инвайт.
Для `chat_join_request` оплата повторно проверяется в БД. Если channel ID не настроен,
показывается сохранённая статическая ссылка.

## Запуск

```bash
export TELEGRAM_BOT_TOKEN=...
python -m bot.bot
```

Служебные команды:

- `/start` — начать воронку;
- `/all` — показать все контентные шаги;
- `/step N` — показать конкретный шаг.

Для локального прохода без оплаты, только когда Prodamus не настроен:

```bash
BOT_SIMULATE_PAYMENT=1 python -m bot.bot
```

## Основные файлы

```text
bot/funnel.json   тексты, кнопки и маршруты
bot/content.py    строгая загрузка snapshot
bot/routing.py    проверка маршрутов
bot/bot.py        polling и UX воронки
bot/payments.py   Prodamus: URL, подпись, разбор callback
bot/webhook.py    быстрый HMAC-verified webhook
bot/channel.py    доступ в каналы
bot/reminders.py  напоминания неоплатившим
```
