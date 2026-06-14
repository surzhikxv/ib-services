"""TDD: форматирование разбора для отправки владельцу в Telegram."""
from kontur.ai.telegram import format_report_for_telegram
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
