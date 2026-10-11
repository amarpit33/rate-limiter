"""Shared types for the limiters."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    """Outcome of one rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_ms: int  # 0 when allowed; otherwise time until a retry can succeed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
