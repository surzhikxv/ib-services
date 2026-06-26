# Релей LLM (форвард-прокси вне РФ)

`api.anthropic.com` из РФ заблокирован, поэтому вызов модели идёт через форвард-прокси
вне РФ (squid на офшоре, напр. Hetzner). Соединение — сквозной TLS через `CONNECT`,
**прокси видит только шифротекст**; данные и БД остаются на РФ-сервере (152-ФЗ).

## Настройка

В `.env`:

```
LLM_API_KEY=...                                  # свой ключ Anthropic (аккаунт thedialog.kz, KZ)
LLM_PROXY_URL=http://user:pass@RELAY_IP:3128     # пусто = напрямую (локально/в KZ)
```

Релей (squid) должен **разрешать egress только на `api.anthropic.com`** (allowlist),
больше никуда. Креды прокси — только в `.env` (в гит не попадают), ротировать при утечке.

## Проверка с РФ-VPS (по порядку)

**1. Дешёвая проба достижимости (без трат токенов — `GET /v1/models`):**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -x "$LLM_PROXY_URL" \
  https://api.anthropic.com/v1/models \
  -H "x-api-key: $LLM_API_KEY" -H "anthropic-version: 2023-06-01"
```

Ждём `200`. (`401` = прокси работает, но ключ битый; `000`/таймаут = прокси не туннелирует.)
Тот же curl **без** `-x` с РФ-VPS должен виснуть/падать — это доказывает, что блок реален
и чинит именно прокси.

**2. Реальный путь кода проекта через прокси:**

```bash
LLM_API_KEY=... LLM_PROXY_URL=... ./.venv/bin/python -c \
"from kontur.ai.llm import AnthropicLLM; import os; \
 print(AnthropicLLM(os.environ['LLM_API_KEY'], proxy_url=os.environ['LLM_PROXY_URL']).complete('Ответь одним словом.','Связь есть?'))"
```

**3. End-to-end через CLI:**

```bash
# сухой прогон без ключа (проверка проводки, токены не тратятся):
./.venv/bin/python -m kontur.cli ai ask "проверка" --show-prompt
# реальный вызов через прокси (нужны LLM_API_KEY + LLM_PROXY_URL в .env):
./.venv/bin/python -m kontur.cli ai ask "одно слово: связь?"
```

> Релей — единая точка отказа для разборов ИИ (воронка и VK-коннектор от него не зависят).
> Перед опорой на него прогнать проверку выше; как fallback без сети — `--show-prompt` / `FakeLLM`.
