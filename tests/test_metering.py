from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.errors import DomainError
from app.schemas import GenerateRequest


def test_retry_returns_identical_response_and_one_event(h):
    body = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 500, "reasoning_tokens": 200}
    a = h.client.post("/generate", json=body, headers=h.headers())
    b = h.client.post("/generate", json=body, headers=h.headers())
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()
    assert a.headers["Idempotency-Replayed"] == "false"
    assert b.headers["Idempotency-Replayed"] == "true"
    usage = h.client.get("/usage", headers=h.headers()).json()
    assert usage["events"] == usage["api_calls"]["used"] == 1
    assert usage["tokens"]["used"] == 1500
    assert usage["cost"]["total_micro_usd"] == 6600


def test_changed_payload_conflicts_without_new_charge(h):
    assert h.client.post("/generate", json={}, headers=h.headers()).status_code == 200
    changed = h.client.post("/generate", json={"input_tokens": 1}, headers=h.headers())
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_conflict"
    assert h.app.state.meter.usage("alpha")["events"] == 1


def test_normalized_defaults_are_same_request(h):
    a = h.client.post("/generate", json={}, headers=h.headers())
    b = h.client.post("/generate", json={"input_tokens": 0}, headers=h.headers())
    assert a.json() == b.json()


def test_concurrent_retries_record_exactly_once(h):
    meter = h.app.state.meter
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(lambda _: meter.record("alpha", "racing-key", GenerateRequest(input_tokens=50)), range(24))
        )
    assert sum(not replay for _, replay in results) == 1
    assert len({r["event_id"] for r, _ in results}) == 1
    assert meter.usage("alpha")["events"] == 1


def test_concurrent_distinct_requests_cannot_overspend_last_call(h):
    with h.app.state.db.transaction() as conn:
        conn.execute("UPDATE plans SET api_call_limit=1 WHERE id='free'")

    def record(i):
        try:
            h.app.state.meter.record("alpha", f"unique-{i}", GenerateRequest())
            return 200
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(record, range(16)))
    assert statuses.count(200) == 1
    assert statuses.count(429) == 15
    assert h.app.state.meter.usage("alpha")["api_calls"]["used"] == 1


def test_exact_1000_api_call_boundary_and_retry_after(h):
    for i in range(1000):
        h.app.state.meter.record("alpha", f"call-{i}", GenerateRequest())
    assert h.app.state.meter.usage("alpha")["api_calls"]["remaining"] == 0
    blocked = h.client.post("/generate", json={}, headers=h.headers(key="call-1001"))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["used"] == blocked.json()["error"]["limit"] == 1000
    assert int(blocked.headers["Retry-After"]) > 0
    # Replay succeeds even though the tenant is now at quota.
    assert h.client.post("/generate", json={}, headers=h.headers(key="call-999")).status_code == 200
    assert h.app.state.meter.usage("alpha")["events"] == 1000


def test_token_boundary_and_rejected_request_can_be_retried(h):
    exact = h.client.post("/generate", json={"input_tokens": 100000}, headers=h.headers())
    assert exact.status_code == 200
    blocked = h.client.post("/generate", json={"output_tokens": 1}, headers=h.headers(key="next"))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["meter"] == "tokens"
    # Rejected requests consume neither a call nor an idempotency key.
    assert h.client.post("/generate", json={}, headers=h.headers(key="next")).status_code == 200
    assert h.app.state.meter.usage("alpha")["events"] == 2


def test_both_quotas_are_checked_atomically(h):
    blocked = h.client.post("/generate", json={"input_tokens": 100001}, headers=h.headers())
    assert blocked.status_code == 429
    assert h.app.state.meter.usage("alpha")["events"] == 0


@pytest.mark.parametrize("status", ["past_due", "unpaid", "incomplete", "incomplete_expired", "paused"])
def test_delinquent_subscription_returns_402(h, status):
    with h.app.state.db.transaction() as conn:
        conn.execute("UPDATE subscriptions SET status=? WHERE tenant_id='alpha'", (status,))
    response = h.client.post("/generate", json={}, headers=h.headers())
    assert response.status_code == 402
    assert response.json()["error"]["subscription_status"] == status
    assert h.app.state.meter.usage("alpha")["events"] == 0


def test_previous_success_replays_after_payment_lapses(h):
    a = h.client.post("/generate", json={}, headers=h.headers())
    with h.app.state.db.transaction() as conn:
        conn.execute("UPDATE subscriptions SET status='past_due' WHERE tenant_id='alpha'")
    b = h.client.post("/generate", json={}, headers=h.headers())
    assert a.json() == b.json()
    assert b.status_code == 200


def test_month_rollover_preserves_historical_idempotency(h):
    now = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)]
    h.app.state.meter.clock = lambda: now[0]
    old, _ = h.app.state.meter.record("alpha", "december", GenerateRequest(input_tokens=100000))
    now[0] = datetime(2027, 1, 1, tzinfo=UTC)
    assert h.app.state.meter.usage("alpha")["tokens"]["used"] == 0
    replay, repeated = h.app.state.meter.record("alpha", "december", GenerateRequest(input_tokens=100000))
    assert repeated and old == replay
    h.app.state.meter.record("alpha", "january", GenerateRequest(input_tokens=100000))
    assert h.app.state.meter.usage("alpha")["events"] == 1
