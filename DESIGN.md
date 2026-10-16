# Usage Metering & Billing Engine: design

Build a Python/FastAPI backend that answers how much a tenant used this UTC
calendar month, the exact modeled cost, and whether another action is allowed.
SQLite is an explicitly permitted capstone database; this project uses its durable
transactions rather than an in-memory counter. No AI provider is called.

## Data and layers

`tenants` contains hashed API credentials. `plans` defines Free (1,000 calls,
100,000 tokens) and Pro (10,000 calls, 1,000,000 tokens). `subscriptions` mirrors
Stripe status. `usage_events` holds one immutable action containing both meters,
token categories, pricing version, integer micro-USD costs, and the original JSON
response. `checkout_requests` persists Checkout idempotency. `webhook_events`,
`jobs`, and `alerts` form a transactional inbox and retryable worker queue.
Numbered SQL migrations create constraints and tenant/month indexes.

HTTP (`app/main.py`, `schemas.py`) validates and authenticates. Domain services
(`metering.py`, `billing.py`, `worker.py`) enforce rules. Persistence (`db.py`,
SQL migrations) owns connections and transactions; `stripe_gateway.py` is the
only payment provider boundary. Stripe secrets are environment-only. Tenant keys
are hashed at rest; seed credentials live in an ignored local file.

## API contract and correctness

- `POST /generate`: `X-API-Key`, `Idempotency-Key`, simulated token counts.
  A `BEGIN IMMEDIATE` transaction checks the existing tenant/key **before** plan
  and quota checks, checks both monthly meters, then inserts one row and its
  response. A UNIQUE(tenant_id, idempotency_key) constraint is a second guard.
  Retrying a successful request returns the stored body, even next month or after
  a plan change. Reusing the key with different normalized input returns 409.
  Failed validation/quota requests are not metered or cached.
- `GET /usage`: tenant-scoped monthly SUM, usage, limits, remaining, exact costs.
  Reaching a limit exactly is allowed. Exceeding either meter returns 429 with
  Retry-After until next UTC month. A delinquent subscription returns 402.
- `POST /billing/checkout`: idempotent creation of a test subscription Checkout;
  creating a session never grants Pro. Server-owned redirect URLs and price.
- `POST /webhooks/stripe`: verify raw bytes and timestamp using Stripe's SDK;
  refuse live events, durably deduplicate and enqueue, then return 202. A worker
  fetches the current Stripe subscription before updating the mirror, so replayed
  or out-of-order notifications do not restore old snapshots. Leased jobs serialize
  work per tenant, retry with backoff, and produce a durable local failure alert.
- `GET /billing/subscription`, `/billing/jobs`: authenticated local sync status.

Token convention: input includes cached input; output includes reasoning. Count
input + output once. Cost = (input - cached) * 2 + cached * 1 + output * 8,
plus 1,000 micro-USD per API call. These are simulated, versioned rates, never
claims about a current model price. One USD = 1,000,000 micro-USD. No floats.

## Scope and verification

Non-goal: charging usage, invoicing, proration, or overages. Stripe's recurring
subscription charge is separate from the modeled usage cost. Calendar month usage
does not reset on upgrade or Stripe renewal. The simulated action and metering
commit are atomic because the action has no external side effect.

Tests cover concurrent duplicate requests, competing quota requests, month
boundaries, tenant isolation, pricing, forged/replayed webhooks, current-state
sync, and worker failure recovery. Real Stripe Checkout is a separate manual
acceptance step requiring the owner's local test account; synthetic tests must
never be presented as proof of an actual Stripe transaction.
