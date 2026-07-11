from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProtocolError(ValueError):
    """A deterministic v2 protocol validation error."""

    code: str
    message: str
    field_errors: list[dict[str, str]]
    details: dict[str, Any]

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def as_error(self, diagnostic_id: str = "diag-contract-v2") -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": False,
            "field_errors": self.field_errors,
            "details": self.details,
            "diagnostic_id": diagnostic_id,
        }
