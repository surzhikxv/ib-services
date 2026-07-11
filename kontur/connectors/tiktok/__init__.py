"""Коннектор TikTok: ингест файлов (userscript-JSON + Overview.csv) → озеро.

В отличие от VK здесь нет живого API: внутренние запросы TikTok Studio
подписаны (webmssdk/X-Bogus), серверно их не воспроизвести, а Business API
владельцем отклонён. Поэтому источник данных — файлы, снятые из залогиненного
браузера владельца:
- ``reader.parse_capture`` — JSON, собранный userscript'ом с эндпоинта
  ``/aweme/v2/data/insight/`` (богатые per-video метрики);
- ``reader.parse_overview`` — нативный CSV-экспорт TikTok Studio (канал-дневная).
"""
