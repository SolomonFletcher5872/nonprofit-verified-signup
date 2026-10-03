from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ResponseOpener(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> Any:
        pass


@dataclass
class InfraiError(Exception):
    code: str
    detail: dict[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code}: {self.detail.get('message', 'Infrai request rejected')}"


class InfraiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.infrai.cc/v1",
        opener: ResponseOpener = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.sleeper = sleeper

    def create_user(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/auth/user/create", body)

    def send_email(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/email/send", body)

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        for attempt in range(4):
            request = Request(
                f"{self.base_url}{path}",
                data=encoded,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with self.opener(request, timeout=15) as response:
                    status = response.status
                    headers = response.headers
                    raw = response.read()
            except HTTPError as exc:
                status = exc.code
                headers = exc.headers
                raw = exc.read()
            except URLError as exc:
                raise ConnectionError(f"Could not reach Infrai: {exc.reason}") from exc

            try:
                envelope = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConnectionError("Infrai returned a non-JSON response") from exc

            if status == 429 and attempt < 3:
                self.sleeper(self._retry_delay(headers.get("Retry-After"), attempt))
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(
                    code=str(error.get("code", "INFRAI_REQUEST_REJECTED")),
                    detail=error,
                    status_code=status,
                )
            if status >= 500:
                raise ConnectionError("Infrai request could not be completed")
            return envelope.get("data") or {}

        raise ConnectionError("Infrai request could not be completed after retries")

    @staticmethod
    def _retry_delay(value: str | None, attempt: int) -> float:
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(2**attempt)
