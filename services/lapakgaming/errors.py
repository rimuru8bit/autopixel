"""Typed errors for Lapakgaming API."""

from __future__ import annotations


class LapakgamingError(Exception):
    """Base class for Lapakgaming client errors."""


class LapakgamingAuthError(LapakgamingError):
    """Authorization/authentication failure."""


class LapakgamingRateLimitError(LapakgamingError):
    """Rate limit exceeded (HTTP 429)."""


class LapakgamingServerError(LapakgamingError):
    """5xx server-side failure from provider."""


class LapakgamingTransportError(LapakgamingError):
    """Network/transport-layer failure."""


class LapakgamingResponseError(LapakgamingError):
    """Invalid/non-parseable response payload."""


class LapakgamingBusinessError(LapakgamingError):
    """Business-level API code failure (code != SUCCESS)."""

    def __init__(self, code: str, message: str = "", data: object | None = None):
        self.code = code
        self.data = data
        super().__init__(message or code)
