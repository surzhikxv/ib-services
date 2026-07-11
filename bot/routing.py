"""Explicit graph of the owned Telegram funnel.

Routes are stored in ``bot/funnel.json``; no external graph resolution is
performed at runtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .content import FUNNEL_PATH

TARIFF_BY_INFO_STEP = {2: "basic", 3: "standard", 4: "premium"}

STAGE_BY_STEP = {
    0: "welcome", 7: "welcome",
    1: "package_choice",
    2: "package_info", 3: "package_info", 4: "package_info",
}

CONFIRM_STEP_BY_TARIFF = {"basic": 6, "standard": 8, "premium": 5}


@dataclass(frozen=True)
class Route:
    kind: str  # "step" | "url" | "pay" | "terminal"
    target: int | None = None
    url: str | None = None
    tariff: str | None = None


def build_routes(snapshot: dict | None = None) -> dict[tuple[int, int, int], Route]:
    """Build ``(step, block, button) -> Route`` with strict validation."""
    snapshot = snapshot or json.loads(Path(FUNNEL_PATH).read_text(encoding="utf-8"))
    if snapshot.get("version") != 1:
        raise ValueError(f"unsupported funnel snapshot version: {snapshot.get('version')!r}")
    steps = snapshot.get("steps", [])
    routes: dict[tuple[int, int, int], Route] = {}
    for step_index, step in enumerate(steps):
        for block_index, block in enumerate(step.get("blocks") or []):
            for button_index, button in enumerate(block.get("buttons") or []):
                raw_route = button.get("route") or {}
                kind = raw_route.get("kind")
                location = (step_index, block_index, button_index)
                if kind not in {"step", "url", "pay", "terminal"}:
                    raise ValueError(f"invalid funnel route at {location}: {kind!r}")
                target = raw_route.get("target")
                if kind == "step" and (
                    not isinstance(target, int) or not 0 <= target < len(steps)
                ):
                    raise ValueError(f"invalid funnel target at {location}: {target!r}")
                routes[location] = Route(
                    kind=kind,
                    target=target,
                    url=raw_route.get("url"),
                    tariff=raw_route.get("tariff"),
                )
    return routes


ENTRY_STEP = 0
