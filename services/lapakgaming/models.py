"""Data models for Lapakgaming API responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ApiEnvelope:
    """Canonical Lapakgaming envelope: {code, data}."""

    code: str
    data: Any = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.code.upper() == "SUCCESS"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ApiEnvelope":
        return cls(
            code=str(payload.get("code", "UNKNOWN_ERROR")),
            data=payload.get("data"),
            raw=payload,
        )
