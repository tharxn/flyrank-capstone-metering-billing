# Build log

## 2026-10-16 - Python track implementation

The owner selected the Usage Metering & Billing Engine capstone and Python track.
Codex read the supplied ten-page brief, including its shared requirements, and
created this dedicated project beside the existing assignment projects. The brief
was treated as a project specification; its public-repository and submission text
did not authorize publishing code, submitting a portal form, or accessing a
payment account.

### Where AI helped

Codex authored the initial design, schema/migration, FastAPI API, tenant-key
authentication, transactional metering, integer pricing, Stripe adapter, durable
webhook jobs, retry/alert handling, tests, local acceptance helpers, Docker/CI
configuration, and submission documentation. This is an AI-assisted implementation;
the owner has not yet reviewed or independently explained the code in this log.

SQLite was chosen because the brief explicitly permits it and Docker Desktop's
engine was not running. The design uses SQLite write transactions and database
constraints to demonstrate correctness under concurrency without another service.
Both demo tenants start Free; no admin endpoint can silently grant Pro.

The Stripe integration was checked against official Checkout and webhook docs.
The worker fetches the current subscription rather than trusting an old event's
snapshot or second-resolution creation timestamp. Tests use synthetic signatures
verified by the real SDK and intercepted SDK HTTP, not a real Stripe transaction.

### Where AI was wrong, and what changed

- The first PDF extraction used the Windows console's default encoding and stopped
  on Unicode. Extraction was rerun with UTF-8 and all remaining pages were read.
- The first virtualenv setup and package download were blocked by the sandbox.
  They were rerun with the required approval; an isolated environment was created.
- An initial SQL INSERT had an extra placeholder. It was corrected to match the
  schema before the metering tests ran.
- The first Stripe HTTPX configuration omitted `allow_sync_methods=True`. SDK
  inspection caught it; the setting was fixed and real-adapter tests now exercise
  Checkout creation and subscription retrieval with intercepted HTTP.
- Initial FastAPI tests inside the Windows sandbox failed because the event loop
  could not create its local sockets. The approved run outside the sandbox passed.
  This environment failure is not presented as a product defect or hidden pass.
- Stripe's old dictionary conversion helper generated deprecation warnings. The
  adapter now uses the SDK's dictionary-compatible objects directly.
- A helper was initially documented as a directly executed script even though it
  imported the app package. It now runs as `python -m scripts.replay_webhook`.
  Tests also cover PowerShell UTF-16 and UTF-8 BOM event files.
- Credential-file writes were made atomic and owner-readable before replacement,
  preventing a normal rewrite from truncating the existing seed keys.

### Verification actually performed

The API was seeded and started on localhost. A real HTTP smoke check sent a
request twice, received identical responses, and observed exactly one new event
costing 6,600 micro-USD. The test suite covers the 1,000-call boundary, concurrent
retries and competing quota writes, inclusive token categories, payment states,
tenant isolation, persistence, webhook forgery/replay, current-state sync,
worker restarts, lease replacement, crash exhaustion, and durable alerts.
Full output is in `evidence/pytest.txt`; requirement proofs are in `EVIDENCE.md`.

Ruff, Python compilation, dependency consistency, Docker Compose configuration,
and Git ignore checks were also run. Docker image build/start, hosted GitHub CI,
and real Stripe Checkout were not run. Third-party deprecation warnings from
Starlette's httpx test compatibility and Stripe/httpx are recorded in the test
output rather than suppressed.

### Remaining owner work

Configure a local Stripe test account, complete a real test Checkout through this
API, capture its event/plan-change proof, and publish a separate public GitHub
repository. A local repository was initialized; no commits, push, public remote,
or portal submission were performed. The brief's request for a public repository
from day one and visible phase commits is not fulfilled by a later upload. Do not
invent a retrospective history. Before submission, review the implementation
and be prepared to explain its transaction, token pricing, and webhook logic.

## 2026-10-16 - Actual Stripe sandbox Checkout acceptance

The owner encountered Stripe's India invitation-only account onboarding. Codex
verified Stripe's current official anonymous sandbox CLI workflow rather than
changing the business country. The owner authorized a temporary sandbox and
provided the email to use for provisioning; the email is not reproduced here.

Stripe's official CLI 1.53.1 was installed project-locally under ignored `.tools`.
A dedicated local CLI config/profile kept this test isolated from other logins.
Anonymous provisioning returned an `rkcs_test_` key and a sandbox expiring on
2026-10-23. Key validation was extended to standard, restricted, and temporary
test secret formats. Live, publishable, incomplete, and whitespace-bearing keys
remain rejected. Fourteen additional validation tests brought the full suite to
**72 passed, 3 third-party deprecation warnings**.

Codex created the real sandbox Pro product and USD 20/month price, configured
the listener secret in ignored `.env`, and started the webhook listener. The
first Checkout call reached the old server with empty Stripe settings and
returned 503. Process inspection confirmed that the old capstone process still
owned port 8000; it was restarted with the new settings. The retry then created
a hosted test Checkout through this API.

Codex completed Stripe's hosted Checkout UI with its published test card and
dummy test contact details, leaving the save-information option unchecked.
Stripe reported the session complete and paid with `livemode=false`. The actual
CLI delivery returned 202, the worker completed on attempt 1, Alpha became
Pro/active with 10,000 calls and 1,000,000 tokens, and Beta remained Free.

The actual Checkout event was retrieved from Stripe and replayed locally twice
with the real listener secret. Both responses were `duplicate`; one event and
one job remained. A forged signature returned 400. A harmless Stripe subscription
metadata update produced a real `customer.subscription.updated` event that also
completed successfully. This session did not cancel the subscription; automated
tests cover the deleted/status paths, and Alpha remains Pro for demonstration.

Credential-free reports are in `evidence/stripe-checkout.json`,
`evidence/stripe-replay.json`, and `evidence/stripe-subscription-update.json`.
Raw provisioning output, API keys, signing secret, claim URL, CLI config, and
raw payment event are ignored locally. The earlier pending Stripe note above
records the initial build status; this entry records the completed acceptance.
Public GitHub creation, commits, hosted CI, and portal submission remain open.

## 2026-10-16 - Publication review

The owner explicitly requested creating the dedicated public GitHub repository,
pushing the project, and checking the PDF requirements before submission. Two
independent read-only reviews compared the brief, implementation, and evidence.
One recovery bug was found: a completed Checkout reservation could block a fresh
attempt after cancellation until the old URL expired.

The worker now expires old reservations on the verified transition to canceled,
in the same database transaction as the subscription update. Repeated canceled
notifications do not release a new pending attempt. Old successful idempotency
keys continue to return their original response. Both regression scenarios pass;
the complete local suite is now **74 passed, 3 third-party warnings**.

The configured GitHub account is `tharxn`. Public commits use its GitHub noreply
address rather than the locally configured personal email. The initial secret
scan found no API/signing/GitHub credentials or private runtime paths among the
publication candidates. The four phases will be represented by today's actual
publication commits; they are not presented as commits made during earlier work.
The public-from-day-one and commit-as-you-build historical requirements were
not met. Dates or earlier development history will not be fabricated.

The dedicated public repository was created at
https://github.com/tharxn/flyrank-capstone-metering-billing. The design/configuration
commit is `282e542`, and the implementation/test commit is `bf8184d`. Docker's
engine was available during this review: a fresh Linux Python 3.14 image installed
the runtime dependencies, seeded an isolated empty volume, ran without root,
and passed the HTTP retry/cost check without host secrets. The temporary
verification container and volume were removed. Full clean-start evidence is in
`evidence/clean-start.json`. Push and hosted CI results will be recorded below
after verification; portal submission remains an owner action.
