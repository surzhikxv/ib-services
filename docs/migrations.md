# Ручные миграции прод-БД

Проект на `Base.metadata.create_all` без Alembic. `create_all` создаёт **только
отсутствующие таблицы** — он НЕ добавляет столбцы в уже существующие. Поэтому
**новые таблицы** доезжают на прод сами при следующем `kontur db init`, а
**добавление столбца в существующую таблицу** требует ручного `ALTER` на боевом
Postgres.

Локальная разработка на SQLite (`data/kontur.sqlite`) пересоздаётся в тестах —
эти ALTER'ы там не нужны, только на прод-Postgres.

Применять по порядку. После применения отметить дату/коммит.

---

## 2026-06-26 — foundation-contract (план `2026-06-25-foundation-contract.md`)

Новые таблицы `content_metrics` и `oauth_tokens` создаются `create_all` автоматически —
ALTER не нужен.

Единственный column-add к существующей таблице:

```sql
-- Task 2: content.last_seen_run_id (сигнал устаревшего/удалённого контента)
ALTER TABLE content ADD COLUMN IF NOT EXISTS last_seen_run_id INTEGER REFERENCES sync_runs(id);
-- проверить: \d content  → last_seen_run_id присутствует
```

Статус: ☐ не применено на прод.
