"""Library errors with stable codes and HTTP statuses (handoff section 7)."""
from __future__ import annotations


class LibraryError(Exception):
    status = 400

    def __init__(self, code: str, message: str, status: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.code, self.message = code, message
        self.status = status or self.status
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def not_found(what: str) -> LibraryError:
    return LibraryError("NOT_FOUND", f"{what} not found", 404)


def conflict(code: str, message: str, **details) -> LibraryError:
    return LibraryError(code, message, 409, details)
