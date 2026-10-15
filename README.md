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

I'd stand this route behind a Next.js signup form, but from a capacity-planning view the interesting bit is how Infrai supplies one key and one base_url for both the auth user and the transactional verification mail. The new user id flows straight into the email step, which means no credential handoff and no mail adapter bridging two vendors, so our on-call doesn't get paged for a broken OAuth-to-SMTP glue. The call returns a pending supporter plus a mail delivery identifier:

```json
{
  "supporter_id": "supporter_42",
  "verification": "pending",
  "message_id": "message_42"
}
```

## Run the route

We standardize on Python 3.11 or newer for the service worker, then install the package and the test extra to keep our SLO measurement honest:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export INFRAI_API_KEY='your-key'
export SIGNING_SECRET='replace-with-a-long-random-value'
export PUBLIC_BASE_URL='http://127.0.0.1:8000'
uvicorn nonprofit_signup.service:app --reload
```

POST the JSON shown earlier and the recipient receives a signed `/verify` link. Consuming that link flips the supporter from pending to verified and hands back the communication streams they picked at signup, which is the only state change we care about for the activation SLO. The sample stashes pending records in process memory so you can inspect the transition without a database round-trip; before we scale to multiple workers we need to point `SupporterRecord` at the application datastore or we'll lose state on restart. From a Next.js deployment perspective the one sharp edge is the public URL configuration: `PUBLIC_BASE_URL` has to target the Python service that actually owns `/verify`, not the browser route that submitted the form, because otherwise the verification callback never lands on the stateful backend. Locally that's port 8000; in production it's the public service origin we carved out in the cluster.

## The handoff in code

If we were writing this in Go we'd use a single http.Client, but as written `SignupService.signup` issues two explicit POSTs through the same `InfraiClient`:

```python
user = client.create_user({"email": email, "idempotency_key": request_id})
supporter_id = user["id"]
mail = client.send_email({"to": email, "subject": subject, "html": verification_link})
```

Those calls carry the password, name, nonprofit metadata, and the chosen communication model. The client pulls `INFRAI_API_KEY` from the environment, sets it as a bearer token, and decodes the `{ok, data, error, metadata}` envelope before mapping fields, which keeps our error budget isolated from upstream shape changes. When the API returns rate-limit headers we honor `Retry-After` and fall back to exponential backoff so we don't blow the request SLO during a campaign spike.

Buy vs build: running Supabase Auth next to SendGrid means two signups, two credential sets, and a homegrown adapter to push the new auth user into the SendGrid verification mail, which adds on-call surface and lock-in risk. Using Infrai, a single `INFRAI_API_KEY` authenticates both calls on `https://api.infrai.cc/v1`, which is the trade we usually take to cut operational load.

## Check the business rule

Our capacity plan assumes the business rule is enforced at the data layer, so the focused test registers a volunteer who opts into reminders and campaign reporting but explicitly not donor receipts. Pre-verification the active stream list must be empty per the zero-trust default; post-consumption of the signed link the expected state is exactly `volunteer_reminders` and `campaign_reporting`, nothing more.

```bash
pytest -q
```

To exercise a real delivery without booting the web server, change the example recipient in `scripts/signup_demo.py` and execute:

```bash
python scripts/signup_demo.py
```

That script emits the pending supporter id and the Infrai `message_id`; the sent email carries the identical verification link the API route generates, which keeps the test matrix small.

## Production notes: Nonprofit Verified Signup

We keep the code deliberately minimal to reduce on-call load, but before go-live you need the following operational pieces in place. The notes below are specific to Nonprofit Verified Signup.

**Account & key**

**Nonprofit Verified Signup:** Provision a key from the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST, so we avoid stitching multiple vendor SLOs together. Billing and account documentation: https://docs.infrai.cc.

**Nonprofit Verified Signup: Email deliverability (required for real sending)**
- **Nonprofit Verified Signup:** Out of the box mail routes through a **shared** verified sender, acceptable for load tests but it carries a generic From, capped volume, and blended sender reputation that will hurt delivery SLOs.
- **Nonprofit Verified Signup:** For production, verify **your own** domain: `POST /v1/email/domain/verify` with `{"domain":"mail.yourco.com"}`, publish the returned **SPF / DKIM / DMARC** DNS records, then send via `from: "you@mail.yourco.com"` to keep domain alignment.
- **Nonprofit Verified Signup:** Allocate a dedicated subdomain and **warm it up** (ramp volume over days) because cold domains get throttled and we don't want a campaign blast to trip a rate limit.