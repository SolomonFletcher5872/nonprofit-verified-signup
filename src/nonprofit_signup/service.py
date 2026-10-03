from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from .infrai_client import InfraiClient, InfraiError


class CommunicationChoices(BaseModel):
    donor_receipts: bool = True
    volunteer_reminders: bool = False
    campaign_reporting: bool = False


class SupporterSignup(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["donor", "volunteer", "organizer"]
    communications: CommunicationChoices


class SignupAccepted(BaseModel):
    supporter_id: str
    verification: Literal["pending"] = "pending"
    message_id: str


class VerificationResult(BaseModel):
    supporter_id: str
    verification: Literal["verified"] = "verified"
    active_streams: list[str]


@dataclass
class SupporterRecord:
    supporter_id: str
    email: str
    choices: CommunicationChoices
    verified: bool = False


class SignupService:
    def __init__(self, client: InfraiClient, signing_secret: str, public_base_url: str) -> None:
        self.client = client
        self.secret = signing_secret.encode("utf-8")
        self.public_base_url = public_base_url.rstrip("/")
        self.supporters: dict[str, SupporterRecord] = {}

    def signup(self, request: SupporterSignup) -> SignupAccepted:
        request_id = str(uuid.uuid4())
        user = self.client.create_user(
            {
                "email": str(request.email),
                "password": request.password,
                "name": request.name,
                "metadata": {"role": request.role, "product": "nonprofit"},
                "mode": "email",
                "idempotency_key": request_id,
            }
        )
        supporter_id = str(user.get("id") or user.get("user_id") or "")
        if not supporter_id:
            raise ConnectionError("User creation response did not include an identifier")

        record = SupporterRecord(supporter_id, str(request.email), request.communications)
        self.supporters[supporter_id] = record
        token = self._sign(supporter_id, record.email)
        query = urlencode({"token": token})
        link = f"{self.public_base_url}/verify?{query}"
        mail = self.client.send_email(
            {
                "to": record.email,
                "subject": "Verify your nonprofit updates",
                "html": (
                    f"<p>Hi {request.name},</p>"
                    f'<p><a href="{link}">Verify your email</a> to activate your selected updates.</p>'
                ),
            }
        )
        message_id = str(mail.get("message_id") or "")
        if not message_id:
            raise ConnectionError("Email response did not include a message identifier")
        return SignupAccepted(supporter_id=supporter_id, message_id=message_id)

    def verify(self, token: str) -> VerificationResult:
        payload = self._read_token(token)
        supporter_id = str(payload["supporter_id"])
        record = self.supporters.get(supporter_id)
        if record is None or record.email != payload["email"]:
            raise ValueError("Verification link does not match a pending signup")
        record.verified = True
        return VerificationResult(
            supporter_id=supporter_id,
            active_streams=self.active_streams(record),
        )

    @staticmethod
    def active_streams(record: SupporterRecord) -> list[str]:
        if not record.verified:
            return []
        choices = record.choices
        return [
            stream
            for stream, enabled in (
                ("donor_receipts", choices.donor_receipts),
                ("volunteer_reminders", choices.volunteer_reminders),
                ("campaign_reporting", choices.campaign_reporting),
            )
            if enabled
        ]

    def _sign(self, supporter_id: str, email: str) -> str:
        payload = {"supporter_id": supporter_id, "email": email, "expires_at": int(time.time()) + 3600}
        packed = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(self.secret, packed.encode(), hashlib.sha256).hexdigest()
        return f"{packed}.{signature}"

    def _read_token(self, token: str) -> dict[str, object]:
        try:
            packed, supplied = token.rsplit(".", 1)
            expected = hmac.new(self.secret, packed.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                raise ValueError("Verification link signature is invalid")
            padding = "=" * (-len(packed) % 4)
            payload = json.loads(base64.urlsafe_b64decode(packed + padding))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Verification link is invalid") from exc
        if int(payload["expires_at"]) < int(time.time()):
            raise ValueError("Verification link has expired")
        return payload


def create_app(service: SignupService | None = None) -> FastAPI:
    app = FastAPI(title="Nonprofit supporter signup")
    configured_service = service

    def current_service() -> SignupService:
        nonlocal configured_service
        if configured_service is None:
            configured_service = SignupService(
                InfraiClient(os.environ["INFRAI_API_KEY"]),
                os.environ["SIGNING_SECRET"],
                os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000"),
            )
        return configured_service

    @app.post("/signups", response_model=SignupAccepted, status_code=202)
    def create_signup(request: SupporterSignup) -> SignupAccepted:
        try:
            return current_service().signup(request)
        except InfraiError as exc:
            client_status = exc.status_code if 400 <= exc.status_code < 500 else 502
            raise HTTPException(status_code=client_status, detail={"code": exc.code, "message": str(exc)}) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/verify", response_model=VerificationResult)
    def verify_signup(token: Annotated[str, Query(min_length=20)]) -> VerificationResult:
        try:
            return current_service().verify(token)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
