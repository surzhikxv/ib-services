"""TDD: форматирование разбора для отправки владельцу в Telegram."""
from kontur.ai.telegram import format_report_for_telegram, split_telegram_text
from kontur.models import AiReport


def test_format_weekly_report():
    r = AiReport(kind="weekly", period="2026-W24", summary="Продажи выросли на 10%.")
    text = format_report_for_telegram(r)
    assert "2026-W24" in text
    assert "Продажи выросли на 10%." in text


def test_format_adhoc_includes_question():
    r = AiReport(kind="adhoc", question="почему меньше продаж?", summary="Короткая неделя.")
    text = format_report_for_telegram(r)
    assert "почему меньше продаж?" in text
    assert "Короткая неделя." in text


def test_long_report_is_split_without_losing_text():
    text = "\n\n".join(f"Блок {index}: " + "текст " * 20 for index in range(12))
    chunks = split_telegram_text(text, limit=180)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 180 for chunk in chunks)
    compact = lambda value: value.replace(" ", "").replace("\n", "")
    assert compact("".join(chunks)) == compact(text)
