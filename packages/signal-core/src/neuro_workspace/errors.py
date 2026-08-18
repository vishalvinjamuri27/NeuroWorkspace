"""Stable domain errors safe to translate at transport boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NeuroError(Exception):
    """Base error with a stable safe code and optional correction details."""

    code: str
    message: str
    status_code: int = 400
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class NotFoundError(NeuroError):
    def __init__(self, message: str = "The requested resource was not found.") -> None:
        super().__init__("not_found", message, 404)


class ConflictError(NeuroError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 409)


class ForbiddenError(NeuroError):
    def __init__(self) -> None:
        super().__init__("not_found", "The requested resource was not found.", 404)


class ValidationError(NeuroError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, message, 422, details or {})
