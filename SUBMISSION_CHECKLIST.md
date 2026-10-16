# Submission readiness

Audited on **2026-10-16** against the supplied ten-page Usage Metering & Billing
Engine capstone brief. This file records implementation and verification results;
it does not represent approval from the internship evaluator.

Repository: [tharxn/flyrank-capstone-metering-billing](https://github.com/tharxn/flyrank-capstone-metering-billing)

## Four build phases

| Phase / gate | Result | Proof |
| --- | --- | --- |
| 1. Design committed | PASS now | `DESIGN.md`, initial design commit `282e542`: schema, plans, API, idempotency, layers, non-goal. |
| 2. Core billing logic | PASS | `evidence/pytest.txt`: retries, concurrent quotas, exact 1,000-call and 100,000-token boundaries, 429/402. |
| 3. Stripe test Checkout changes Free -> Pro | PASS | `evidence/stripe-checkout.json`: hosted session complete/paid, signed webhook 202, job done, Alpha Pro and Beta Free. |
| 4. Cost and finalization | PASS | Integer rates and rollups match; README, diagram, manifest, build log, and one proof per Section 6 requirement are present in `EVIDENCE.md`. |

## Acceptance probes and shared requirements

- [x] Probe 1: same billable request/key twice returns identical results and one event.
- [x] Probe 2: exact quota boundary is allowed; next request returns explained 429/402.
- [x] Probe 3: actual Stripe sandbox Checkout changes the authenticated tenant's plan and limits.
- [x] Probe 4: forged signature returns 400; two locally signed replays of the real event remain duplicates with one job.
- [x] Probe 5: cached input is discounted; reasoning is counted within output once; `/usage` matches exact integer cost.
- [x] Layered HTTP, domain logic, and persistence.
- [x] Boundary validation returns clean 4xx responses.
- [x] Durable background job with backoff, lease recovery, retry limit, and failure alert.
- [x] Real persistence, numbered migration, constraints/indexes, and tenant isolation.
- [x] Metering, Checkout, and webhook idempotency.
- [x] Secrets excluded from Git/Docker, tenant keys hashed, provider errors sanitized.
- [x] Simulated per-call AI usage cost attributed to tenant and protected by quotas; no live AI call or AI key required.

Detailed test names, output, and actual Stripe proof: [EVIDENCE.md](EVIDENCE.md).

## Submission pack and reproduction

- [x] Dedicated public repository with lowercase hyphenated name.
- [x] `README.md`: architecture diagram, plans/prices, exact install/seed/run, limitations.
- [x] `capstone.yaml`: working run, seed, test, base URL, and endpoint definitions.
- [x] `EVIDENCE.md`: one proof per required feature, with actual Stripe evidence distinguished from fixtures.
- [x] `BUILDLOG.md`: AI assistance, mistakes/corrections, validation, and historical exceptions.
- [x] `.env.example`: safe placeholders for every supported environment setting.
- [x] Local suite: **74 passed**, plus lint, formatting, dependency, and whitespace checks.
- [x] Fresh Linux Docker build, isolated seed/run, HTTP retry/cost probe, and no host secrets.
- [x] Project pushed to public `main`; GitHub Actions passed on Python 3.12 and
  3.14, with **74 tests on each version**, plus lint and formatting checks.
  [Captured CI evidence](evidence/github-ci.json) /
  [successful run](https://github.com/tharxn/flyrank-capstone-metering-billing/actions/runs/38945849339).

The actual sandbox test occurred on 2026-10-16 and its temporary access expires
2026-10-23. The repository contains credential-free evidence; reproducing the
remote payment flow requires a new authorized sandbox after expiry. The deletion
handler is automated-tested; the recorded real Stripe session exercised Checkout
and subscription update. SQLite is explicitly permitted. Invoices, proration,
overages, usage alerts, and reconciliation are optional stretch goals.

## History timing and owner steps

- **PASS day-one publication:** The repository was published on 2026-10-16,
  the first recorded build day. Initial implementation happened locally earlier
  that day. The PDF does not explicitly require a public remote before the first
  implementation change; we do not claim it was public throughout development.
- **EXCEPTION:** commits made as each development phase was originally built.
  Today's design/implementation/evidence commits are genuine, but do not recreate
  an earlier phase-by-phase development history. This should be disclosed to the
  evaluator if strict compliance is required.
- [ ] Owner has reviewed the code and can explain any selected 2-3 lines, especially
  `MeterService.record`, inclusive-token pricing, raw-body webhook verification,
  and durable worker retry/fencing behavior. This requires the owner's self-check.
- [ ] Owner submits the **repository URL** through the portal form. Do not submit
  a ZIP or paste the codebase. No portal submission has been performed here.
