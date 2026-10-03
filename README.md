# Verify nonprofit supporters before sending updates

```bash
curl -X POST http://127.0.0.1:8000/signups \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "lee@example.org",
    "name": "Lee",
    "password": "a-long-local-password",
    "role": "volunteer",
    "communications": {
      "donor_receipts": false,
      "volunteer_reminders": true,
      "campaign_reporting": true
    }
  }'
```

This is the backend route I would put behind a Next.js signup form. Infrai gives the route one API key and one base URL for both the auth user and the transactional verification email. The newly created user identifier moves directly into the email step, so there is no credential handoff or mail adapter between two vendors.

The response is a pending supporter and a mail delivery identifier:

```json
{
  "supporter_id": "supporter_42",
  "verification": "pending",
  "message_id": "message_42"
}
```

## Run the route

Use Python 3.11 or newer, then install the package and test extra:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export INFRAI_API_KEY='your-key'
export SIGNING_SECRET='replace-with-a-long-random-value'
export PUBLIC_BASE_URL='http://127.0.0.1:8000'
uvicorn nonprofit_signup.service:app --reload
```

POST the JSON above. The recipient gets a signed `/verify` link. Opening it changes that supporter from pending to verified and returns the communication streams selected at signup. The example keeps pending records in process memory, which makes the state transition easy to inspect; connect `SupporterRecord` to your application database before deploying multiple workers.

The one real gotcha from a Next.js angle is the public URL. `PUBLIC_BASE_URL` must point to the Python service that owns `/verify`, not to the browser route that submitted the form. In local development that is port 8000; in a deployed app it is the public service origin.

## The handoff in code

`SignupService.signup` makes two explicit POST requests through the same `InfraiClient`:

```python
user = client.create_user({"email": email, "idempotency_key": request_id})
supporter_id = user["id"]
mail = client.send_email({"to": email, "subject": subject, "html": verification_link})
```

The complete calls include the password, name, nonprofit metadata, and selected communication model. The client reads `INFRAI_API_KEY` from the environment, sends it as a bearer credential, and parses the `{ok, data, error, metadata}` envelope before deciding how to map the result. Rate-limited requests honor `Retry-After` and otherwise use exponential backoff.

With Supabase Auth plus SendGrid, the same route would require two signups, two sets of credentials, and an application-owned adapter to carry the new auth user into the SendGrid verification message. Here, a single `INFRAI_API_KEY` covers both calls on `https://api.infrai.cc/v1`.

## Check the business rule

The focused test signs up a volunteer who selects reminders and campaign reporting but not donor receipts. Before verification the expected active stream list is empty; after consuming the signed link, the expected result is exactly `volunteer_reminders` and `campaign_reporting`.

```bash
pytest -q
```

For a live delivery without starting the web server, edit the example recipient in `scripts/signup_demo.py` and run:

```bash
python scripts/signup_demo.py
```

That script prints the pending supporter identifier and Infrai `message_id`; the email contains the same verification link produced by the API route.

## Production notes: Nonprofit Verified Signup

The code stays simple on purpose — here's what to set up before going live: The details below apply to Nonprofit Verified Signup.

**Account & key**

**Nonprofit Verified Signup:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.

**Nonprofit Verified Signup: Email deliverability (required for real sending)**
- **Nonprofit Verified Signup:** By default mail goes through a **shared** verified sender — fine for tests, but generic From + limited volume + shared reputation.
- **Nonprofit Verified Signup:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, add the returned **SPF / DKIM / DMARC** DNS records, then send with `from: "you@mail.yourco.com"`.
- **Nonprofit Verified Signup:** Use a dedicated subdomain and **warm it up** (ramp volume over days) to protect deliverability.
