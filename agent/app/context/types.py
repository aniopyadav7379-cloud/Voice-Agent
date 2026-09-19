from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextBundle:
    fast_context: list[dict[str, Any]] = field(default_factory=list)
    deep_context: list[Any] = field(default_factory=list)
    live_data: dict[str, Any] | None = None
    sources_queried: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
