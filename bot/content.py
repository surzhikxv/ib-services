"""Owned funnel snapshot → immutable runtime structure.

``bot/funnel.json`` is the source of truth for texts, media blocks, buttons and
their layout. User-facing copy is loaded byte-for-byte without re-escaping.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FUNNEL_PATH = Path(__file__).resolve().parent / "funnel.json"


@dataclass(frozen=True)
class Button:
    """Button under a funnel message; title is preserved verbatim."""

    title: str
    kind: str  # "postback" | "web_url"
    url: str | None = None

    @property
    def is_url(self) -> bool:
        return self.kind == "web_url"

    @property
    def has_real_url(self) -> bool:
        """Whether Telegram can use the stored URL without runtime resolution."""
        return self.is_url and (self.url or "").startswith(("http://", "https://"))


@dataclass(frozen=True)
class Block:
    """One funnel block equals one Telegram message."""

    media_type: str
    text: str | None
    media: dict | None
    buttons: tuple[Button, ...]
    button_distribution: tuple[int, ...] | None
    formatting: str | None

    @property
    def is_text(self) -> bool:
        return self.media_type == "text"

    @property
    def media_link(self) -> str | None:
        if self.media and isinstance(self.media, dict):
            link = self.media.get("link")
            if isinstance(link, str) and link:
                return link
        return None


@dataclass(frozen=True)
class Step:
    """A funnel step containing zero or more messages."""

    index: int
    title: str
    top_type: str
    blocks: tuple[Block, ...] = field(default_factory=tuple)

    @property
    def is_stub(self) -> bool:
        return len(self.blocks) == 0


def _parse_buttons(raw_buttons: list | None) -> tuple[Button, ...]:
    return tuple(
        Button(
            title=button.get("title", ""),
            kind=button.get("kind", "postback"),
            url=button.get("url"),
        )
        for button in (raw_buttons or [])
    )


def _parse_block(raw_block: dict) -> Block:
    media_type = raw_block.get("media_type") or "text"
    dist = raw_block.get("button_distribution")
    return Block(
        media_type=media_type,
        text=raw_block.get("text") if media_type == "text" else None,
        media=raw_block.get("media") if isinstance(raw_block.get("media"), dict) else None,
        buttons=_parse_buttons(raw_block.get("buttons")),
        button_distribution=tuple(dist) if isinstance(dist, list) else None,
        formatting=raw_block.get("formatting"),
    )


def load_steps(snapshot_path: Path | str = FUNNEL_PATH) -> list[Step]:
    """Load the versioned owned snapshot in its declared order."""
    data = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"unsupported funnel snapshot version: {data.get('version')!r}")
    steps: list[Step] = []
    for index, raw_step in enumerate(data.get("steps", [])):
        if raw_step.get("index") != index:
            raise ValueError(
                f"funnel step index mismatch: expected {index}, got {raw_step.get('index')!r}"
            )
        steps.append(
            Step(
                index=index,
                title=raw_step.get("title", ""),
                top_type=raw_step.get("kind", ""),
                blocks=tuple(_parse_block(block) for block in (raw_step.get("blocks") or [])),
            )
        )
    return steps
