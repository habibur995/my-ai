"""Shared types and exception hierarchy for free LLM provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelInfo:
    """Normalized metadata for a free-tier model exposed in FreeAI Hub."""

    id: str
    label: str
    provider_id: str
    tags: list[str] = field(default_factory=list)
    context_k: int = 128
    vision: bool = False
    coding_rank: int = 60
    speed_rank: int = 70
    free_verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider_id": self.provider_id,
            "tags": list(self.tags),
            "context_k": self.context_k,
            "vision": self.vision,
            "coding_rank": self.coding_rank,
            "speed_rank": self.speed_rank,
            "free_verified": self.free_verified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfo":
        return cls(
            id=str(data["id"]),
            label=str(data.get("label") or data["id"]),
            provider_id=str(data.get("provider_id", "")),
            tags=[str(t) for t in data.get("tags", [])],
            context_k=int(data.get("context_k", 128)),
            vision=bool(data.get("vision", False)),
            coding_rank=int(data.get("coding_rank", 60)),
            speed_rank=int(data.get("speed_rank", 70)),
            free_verified=bool(data.get("free_verified", True)),
        )


class ProviderError(Exception):
    """Base error raised by a model provider adapter."""


class RateLimitError(ProviderError):
    """Raised when a free-tier provider returns HTTP 429 / quota exhausted."""


class ProviderAuthError(ProviderError):
    """Raised when an API key is invalid, missing, or unauthorized."""
