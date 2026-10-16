# Acceptance evidence

Captured locally on **2026-10-16** with Python 3.14 on Windows. Automated tests
and a completed hosted Checkout in an actual temporary Stripe sandbox are
recorded separately below. No real payment was processed.
Full test names and results are preserved in [evidence/pytest.txt](evidence/pytest.txt).

Final captured suite result:

```text
74 passed, 3 warnings in 49.55s
```

The three warnings are third-party deprecations from the Starlette test client
and Stripe/httpx request encoding; their full text is retained in the log.

## Requirements from Section 6

| Requirement | Status and pasted proof |
| --- | --- |
| A billable action creates exactly one usage event, even under retries | PASS: `test_retry_returns_identical_response_and_one_event PASSED`. Live HTTP transcript below also reports `identical_response: true` and `new_events: 1`. |
| Proof that double-counting cannot happen on retries | PASS: `test_concurrent_retries_record_exactly_once PASSED`: 24 simultaneous attempts, one new event, 23 replays. `test_database_itself_prevents_duplicate_usage PASSED` verifies the UNIQUE constraint itself. The guarantee applies to this atomic simulated action. |
| Usage is checked against the tenant's plan and excess requests rejected | PASS: `test_exact_1000_api_call_boundary_and_retry_after PASSED`; exactly 1,000 successful Free calls, call 1,001 blocked. `test_token_boundary_and_rejected_request_can_be_retried PASSED` verifies exactly 100,000 tokens and the next token. |
| Correct 429 / 402 and explanatory responses | PASS: the API boundary test checks 429, used=limit=1,000, and positive `Retry-After`. `test_delinquent_subscription_returns_402[past_due] PASSED` checks 402 and the exact subscription status. Rejections add zero usage. |
| Monthly usage rolls up into a cost per tenant | PASS: `test_retry_returns_identical_response_and_one_event PASSED` asserts `/usage` = 1 event, 1 call, 1,500 tokens, 6,600 micro-USD. `test_month_rollover_preserves_historical_idempotency PASSED` verifies December/January isolation. |
| Correct cached-input, reasoning, and output pricing | PASS: `test_cached_and_reasoning_tokens_are_inclusive_and_integer PASSED` and `test_cached_input_is_cheaper_and_reasoning_does_not_add_cost PASSED`. Calculation below reproduces the exact result. |
| Pricing constants pinned with correct totals | PASS: `app/config.py`: version `simulated-usd-v1`, API=1,000, input=2, cached=1, output=8 micro-USD per unit. Integer result 6,600 = exact string `0.006600`. No current model pricing claim. |
| Subscription Checkout works end-to-end in real Stripe test mode | **PASS actual Stripe sandbox**: [stripe-checkout.json](evidence/stripe-checkout.json) records hosted Checkout `status=complete`, `payment_status=paid`, USD 2,000 cents, `livemode=false`, and signed webhook delivery returning 202. The worker completed on attempt 1 and changed Alpha Free -> Pro; `/usage` shows 10,000 calls / 1,000,000 tokens. Beta remained Free. |
| Webhooks verify signatures, ignore duplicates, and update plan/status | **PASS actual delivery and replay**: [stripe-replay.json](evidence/stripe-replay.json) records forged signature -> 400 and the real Checkout event replayed twice -> 200 duplicate, retaining one event and one job. Replays were signed locally. [stripe-subscription-update.json](evidence/stripe-subscription-update.json) records an actual Stripe subscription.updated delivery and processed job. Cancellation/status variants also pass automated tests. |
| Tenants, plans, subscriptions, usage events; isolated customer data | PASS: `test_migrations_and_seed_are_repeatable PASSED`, `test_tenant_usage_and_idempotency_keys_are_isolated PASSED`, `test_usage_persists_across_new_service_instances PASSED`. Both usage and job/alert reads are tenant scoped. |
| README, architecture diagram, setup, and required pack files | PASS: README contains Mermaid architecture, exact PowerShell setup/seed/run steps, pricing/limits, and limitations. `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example`, and `DESIGN.md` are present. Local file and configuration checks are described below. |

## Live localhost transcript

Command, after starting `python -m app.cli run`:

```text
python scripts/smoke.py
```

Actual response from the running server (not an in-process fixture):

```json
{
  "health": {
    "status": "ok",
    "database": "sqlite",
    "stripe_mode": "test"
  },
  "first_status": 200,
  "retry_status": 200,
  "identical_response": true,
  "new_events": 1,
  "request_cost_micro_usd": 6600,
  "request_cost_usd": "0.006600"
}
```

Input: 1,000 inclusive input tokens, 400 cached input, 500 inclusive output,
200 reasoning. Total metered tokens = 1,000 + 500 = 1,500.

```text
API                         1 * 1000 = 1000 micro-USD
Uncached input   (1000 - 400) *    2 = 1200 micro-USD
Cached input              400 *    1 =  400 micro-USD
All output                500 *    8 = 4000 micro-USD
Reasoning subset          already included in output
Total                                  6600 micro-USD = USD 0.006600
```

## Shared capstone requirements

| Requirement | Proof |
| --- | --- |
| Layered data / logic / HTTP | `db.py` + SQL migrations / domain services / `main.py` + schemas; diagram and design explain the boundaries. |
| Clean boundary validation | `test_invalid_token_input_is_clean_422_without_usage` passes for negative, fractional, boolean, string, excessive, invalid subset, unknown tenant field, null, and non-object input. |
| Background job with retries and failure alert | `test_worker_retries_then_recovers_and_survives_restart PASSED`; `test_terminal_failure_has_durable_alert_without_secret_logs PASSED`: three attempts, dead job, one durable alert, sanitized failure log. |
| Persistence, migrations, indexes, tenant isolation | Versioned SQL creates relational constraints and tenant/month indexes; persistence and tenant-isolation tests pass. |
| Idempotency | Both database UNIQUE constraint and concurrent metering tests pass; Checkout propagates a persisted key into Stripe, and webhook IDs are unique. |
| Secret hygiene | `.env`, demo credential file, and virtualenv are confirmed ignored. Seed keys are random and hashed at rest. No actual Stripe key is present in source; tests generate synthetic keys at runtime. No provider exception payloads are logged. |
| AI cost and budget guard if applicable | No AI provider is called. Simulated per-call costs are attributed to the authenticated tenant and protected by call/token quotas before insertion. |

Worker recovery also has proof for stale-owner fencing and a crashed final
attempt: `test_stale_worker_cannot_commit_after_replacement_takes_lease PASSED`
and `test_crashed_final_attempt_becomes_dead_with_alert PASSED`.

The publication review also verified cancellation/recheckout recovery:
`test_verified_cancellation_allows_new_checkout_and_preserves_old_replay PASSED`
and `test_repeated_canceled_notification_does_not_unlock_new_checkout PASSED`.
Only a verified transition to canceled releases the previous Checkout reservation;
the old successful key still replays, and a later canceled notification cannot
release a new attempt's reservation.

## Local checks

```text
python -m ruff check .
All checks passed!

python -m ruff format --check .
23 files already formatted

python -m pip check
No broken requirements found.

docker compose config --quiet
(exit 0)

git check-ignore .env data/demo-credentials.json .venv/Scripts/python.exe
.env
data/demo-credentials.json
.venv/Scripts/python.exe
```

During the final publication review, Docker became available and a fresh Linux
image was built and tested. [clean-start.json](evidence/clean-start.json) records
an isolated empty volume, successful seed/run, non-root execution, healthy API,
identical retry responses, one event, and a cost of 6,600 micro-USD. No host
credentials or database were mounted, and the temporary container/volume were
removed afterward.

The dedicated public repository is
[tharxn/flyrank-capstone-metering-billing](https://github.com/tharxn/flyrank-capstone-metering-billing).
The design is committed in `282e542`; the implementation and test suite in
`bf8184d`. These are actual publication-time commits. The earlier development
was local on the same first recorded build day, 2026-10-16. Same-day publication
satisfies the brief's day-one wording; the original commit-as-you-build sequence
was not preserved. GitHub Actions passed on Python 3.12 and 3.14 with 74 tests
on each version; [github-ci.json](evidence/github-ci.json) retains run/commit links
and captured test/lint/format results. Final submission status is tracked in
[SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md).

## Actual Stripe sandbox acceptance: completed

India's invitation-only account signup blocked the initial account-based setup.
With the owner's authorization, Stripe CLI **1.53.1** provisioned an anonymous
sandbox through the documented `sandbox create` command. Its temporary key and
listener signing secret were saved only in ignored local files. The sandbox
expires **2026-10-23**. This is an actual Stripe-hosted test environment, separate
from the offline fixtures. Hosted Checkout explicitly displayed that no real
payments are processed.

- [x] Provision test sandbox and create a recurring Pro price: **USD 20/month**.
- [x] Create hosted Checkout through `POST /billing/checkout` and complete it
  through Stripe's browser form with its test card.
- [x] Receive actual `checkout.session.completed` through the Stripe CLI listener;
  verify its signature and process its durable job.
- [x] Verify Alpha is Pro/active with the configured limits while Beta stays Free.
- [x] Reject a forged signature and replay the real Checkout event twice without
  adding another event or job. These replays were signed locally using the real
  listener secret; they were not new deliveries from Stripe.
- [x] Make a harmless metadata update to the test subscription and verify the
  actual `customer.subscription.updated` delivery and completed job. No pricing
  or payment terms changed; Alpha remains Pro.

Selected proof from [stripe-checkout.json](evidence/stripe-checkout.json):

```text
Checkout session: cs_test_a1r9PkmbpUPghFrftVzLwyZcSmUCZ6SHonPzksadoQZMkC4fkQLgqzXe5m
Stripe status: complete; payment_status: paid; currency: usd; amount_total: 2000
Subscription: sub_1URFPBExbfv6mT3S0uWrpG1B (active, livemode=false)
Event: evt_1URFPDExbfv6mT3SxbShiEFo (checkout.session.completed, processed)
CLI delivery: [202] POST http://127.0.0.1:8000/webhooks/stripe
Worker: done; attempts: 1; alerts: []
Alpha: pro; API-call limit: 10000; token limit: 1000000
Beta: free; API-call limit: 1000; token limit: 100000
```

The browser returned to the application's success URL after hosted payment;
[the captured return-page screenshot](evidence/stripe-checkout-return.jpg)
records that navigation. The paid status and Pro entitlement are verified by the
Stripe API and database reports above, not inferred from the redirect alone.

The actual subscription deletion path was not exercised against Stripe in this
session; cancellation is covered by the automated tests. The successful Pro
test subscription was kept for local demonstration. Sandbox expiry will limit
future reproduction, so retained credential-free evidence is important. Fresh
test credentials, price, and listener setup are needed after expiry.
