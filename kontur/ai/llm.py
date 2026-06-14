"""Слой языковой модели за узким интерфейсом — провайдер легко поменять.

По умолчанию — Claude через официальный SDK Anthropic (модель claude-opus-4-8,
adaptive thinking). Если у клиента ключ другого провайдера, заменяется одной
реализацией LLMClient; всё остальное (дайджест, промпты, хранение) не меняется.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "claude-opus-4-8"


@runtime_checkable
class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str:
        ...


class FakeLLM:
    """Подменная модель для тестов и dry-run (ключ не нужен)."""

    model = "fake"

    def __init__(self, reply: str = "(заглушка ответа модели)"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


class AnthropicLLM:
    """Claude через SDK Anthropic. Импорт ленивый — пакет нужен только здесь (extra `ai`)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, effort: str = "medium",
                 max_tokens: int = 4000):
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        import anthropic  # ленивый импорт

        client = anthropic.Anthropic(api_key=self.api_key)
        # стримим (на случай длинного разбора) и забираем финальное сообщение
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            message = stream.get_final_message()
        return "".join(b.text for b in message.content if b.type == "text")
