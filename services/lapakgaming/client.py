"""Lapakgaming API client (Phase 1 foundation)."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import config
from services.lapakgaming.errors import (
    LapakgamingAuthError,
    LapakgamingBusinessError,
    LapakgamingError,
    LapakgamingRateLimitError,
    LapakgamingResponseError,
    LapakgamingServerError,
    LapakgamingTransportError,
)
from services.lapakgaming.models import ApiEnvelope

logger = logging.getLogger(__name__)


BUSINESS_ERROR_MAP: dict[str, type[LapakgamingBusinessError]] = {
    "UNAUTHORIZED": LapakgamingBusinessError,
    "PRODUCT_NOT_FOUND": LapakgamingBusinessError,
    "PRODUCT_EMPTY": LapakgamingBusinessError,
    "PROVIDER_NOT_FOUND": LapakgamingBusinessError,
    "PRICE_NOT_MATCH": LapakgamingBusinessError,
    "PROVIDER_INACTIVE": LapakgamingBusinessError,
    "TID_NOT_FOUND": LapakgamingBusinessError,
    "USER_ID_CONTAIN_SPACE": LapakgamingBusinessError,
    "STOCK_NOT_FOUND": LapakgamingBusinessError,
    "USER_ID_EMPTY": LapakgamingBusinessError,
    "INSUFFICIENT_BALANCE": LapakgamingBusinessError,
    "SYSTEM_ERROR": LapakgamingBusinessError,
    "UNKNOWN_ERROR": LapakgamingBusinessError,
    "NOT_ALLOWED": LapakgamingBusinessError,
}


def redact_sensitive(value: Any) -> Any:
    """Redact sensitive values in loggable structures."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            lk = k.lower()
            if lk in {
                "authorization",
                "secret_key",
                "api_key",
                "token",
                "password",
                "cookies",
                "cookie",
            }:
                out[k] = "***REDACTED***"
            else:
                out[k] = redact_sensitive(v)
        return out
    if isinstance(value, list):
        return [redact_sensitive(x) for x in value]
    return value


class LapakgamingClient:
    """Minimal resilient client for Lapakgaming endpoints."""

    def __init__(
        self,
        *,
        env: str | None = None,
        secret_key: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ):
        self.env = (env or config.LAPAK_ENV).lower()
        self.secret_key = secret_key or config.LAPAK_SECRET_KEY
        self.timeout_seconds = timeout_seconds or config.LAPAK_TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else config.LAPAK_MAX_RETRIES

        if not self.secret_key:
            raise LapakgamingAuthError("LAPAK_SECRET_KEY is empty")

        if self.env == "prod":
            self.base_url = config.LAPAK_BASE_URL_PROD.rstrip("/")
        else:
            self.base_url = config.LAPAK_BASE_URL_DEV.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "autopixel-lapak-client/1.0",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> ApiEnvelope:
        url = f"{self.base_url}/{path.lstrip('/')}"
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, method=method.upper(), headers=self._headers(), data=body)

        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw = resp.read().decode("utf-8")
                    parsed = json.loads(raw)
                    envelope = ApiEnvelope.from_dict(parsed)

                    if not envelope.is_success:
                        exc_cls = BUSINESS_ERROR_MAP.get(envelope.code, LapakgamingBusinessError)
                        raise exc_cls(code=envelope.code, data=envelope.data)

                    return envelope

            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise LapakgamingAuthError("Unauthorized (401). Check key/IP allowlist") from exc

                if exc.code == 429:
                    if attempt < attempts:
                        self._sleep_backoff(attempt)
                        continue
                    raise LapakgamingRateLimitError("Rate limited (429)") from exc

                if 500 <= exc.code < 600:
                    if attempt < attempts:
                        self._sleep_backoff(attempt)
                        continue
                    raise LapakgamingServerError(f"Server error ({exc.code})") from exc

                # 4xx others
                try:
                    body_raw = exc.read().decode("utf-8")
                    payload = json.loads(body_raw)
                    envelope = ApiEnvelope.from_dict(payload)
                    if not envelope.is_success:
                        raise LapakgamingBusinessError(code=envelope.code, data=envelope.data)
                except LapakgamingBusinessError:
                    raise
                except Exception:
                    pass
                raise LapakgamingError(f"HTTP error ({exc.code})") from exc

            except urllib.error.URLError as exc:
                if attempt < attempts:
                    self._sleep_backoff(attempt)
                    continue
                raise LapakgamingTransportError(f"Transport error: {exc.reason}") from exc

            except json.JSONDecodeError as exc:
                raise LapakgamingResponseError("Invalid JSON response") from exc

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        base = min(5.0, 0.5 * (2 ** (attempt - 1)))
        time.sleep(base + random.uniform(0.05, 0.35))

    def get_categories(self) -> ApiEnvelope:
        logger.info("Lapak get_categories")
        return self._request("GET", config.LAPAK_PATH_GET_CATEGORIES)

    def get_products(self, category_code: str | None = None) -> ApiEnvelope:
        path = config.LAPAK_PATH_GET_PRODUCTS
        if category_code:
            delim = "&" if "?" in path else "?"
            path = f"{path}{delim}{urllib.parse.urlencode({'category_code': category_code})}"
        logger.info("Lapak get_products category=%s", category_code)
        return self._request("GET", path)

    def get_all_products(self) -> ApiEnvelope:
        logger.info("Lapak get_all_products")
        return self._request("GET", config.LAPAK_PATH_GET_ALL_PRODUCTS)
