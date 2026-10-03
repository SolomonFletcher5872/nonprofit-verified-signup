from typing import Any

from nonprofit_signup.service import CommunicationChoices, SignupService, SupporterSignup


class RecordingClient:
    def __init__(self) -> None:
        self.created: dict[str, Any] = {}
        self.sent: dict[str, Any] = {}

    def create_user(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created = body
        return {"id": "supporter_42"}

    def send_email(self, body: dict[str, Any]) -> dict[str, Any]:
        self.sent = body
        return {"message_id": "message_42"}


def test_verification_activates_only_the_selected_communication_streams() -> None:
    client = RecordingClient()
    service = SignupService(client, "test-signing-secret", "https://nonprofit.example")  # type: ignore[arg-type]
    accepted = service.signup(
        SupporterSignup(
            email="lee@example.org",
            name="Lee",
            password="a-long-test-password",
            role="volunteer",
            communications=CommunicationChoices(
                donor_receipts=False,
                volunteer_reminders=True,
                campaign_reporting=True,
            ),
        )
    )

    record = service.supporters[accepted.supporter_id]
    assert service.active_streams(record) == []
    token = client.sent["html"].split("token=", 1)[1].split('"', 1)[0]
    verified = service.verify(token)

    assert verified.active_streams == ["volunteer_reminders", "campaign_reporting"]
    assert client.created["email"] == client.sent["to"] == "lee@example.org"
    assert client.created["idempotency_key"]
    assert accepted.message_id == "message_42"
