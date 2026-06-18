"""
Standardized error types and response envelope for the Lenticularis API.

Error codes follow the vocabulary in .ai/instructions/07-api-conventions.md:
  AUTH_REQUIRED, PERMISSION_DENIED, ENTITY_NOT_FOUND, VALIDATION_FAILED,
  CONFLICT, INTERNAL_ERROR
"""
from __future__ import annotations


class AppException(Exception):
    """Raise instead of HTTPException to emit the RFC7807 envelope with a typed code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _envelope(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}
