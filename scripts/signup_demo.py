import os

from nonprofit_signup.infrai_client import InfraiClient
from nonprofit_signup.service import CommunicationChoices, SignupService, SupporterSignup


service = SignupService(
    InfraiClient(os.environ["INFRAI_API_KEY"]),
    os.environ["SIGNING_SECRET"],
    os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000"),
)
result = service.signup(
    SupporterSignup(
        email="alex@example.org",
        name="Alex Morgan",
        password="replace-this-demo-password",
        role="volunteer",
        communications=CommunicationChoices(
            donor_receipts=False,
            volunteer_reminders=True,
            campaign_reporting=True,
        ),
    )
)
print(result.model_dump_json(indent=2))
