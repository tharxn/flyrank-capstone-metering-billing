import copy
import time

import pytest

from app.config import Settings
from app.main import create_app
from app.worker import Worker
from tests.conftest import event_object, subscription_object


def test_mock_checkout_to_signed_webhook_upgrades_tenant(h):
    a = h.client.post("/billing/checkout", headers=h.headers())
    b = h.client.post("/billing/checkout", headers=h.headers())
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json() and len(h.gateway.checkouts) == 1
    assert h.app.state.meter.usage("alpha")["plan"] == "free"
    h.gateway.remote["sub_fixture_alpha"] = subscription_object()
    result = h.post_event(event_object(event_type="checkout.session.completed"))
    assert result.status_code == 202
    assert h.app.state.meter.usage("alpha")["plan"] == "free"  # Durably queued, not yet applied.
    assert h.app.state.worker.run_once()
    usage = h.app.state.meter.usage("alpha")
    assert usage["plan"] == "pro"
    assert usage["api_calls"]["limit"] == 10000
    assert usage["tokens"]["limit"] == 1000000
    assert h.app.state.meter.usage("beta")["plan"] == "free"


def test_forged_signature_changes_nothing(h):
    response = h.client.post("/webhooks/stripe", json=event_object(), headers={"Stripe-Signature": "t=1,v1=forged"})
    assert response.status_code == 400
    assert h.app.state.billing.jobs("alpha")["jobs"] == []
    with h.app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
    assert h.app.state.meter.usage("alpha")["plan"] == "free"


def test_old_signature_is_rejected(h):
    assert h.post_event(event_object(), timestamp=int(time.time()) - 600).status_code == 400


def test_webhook_replay_has_one_durable_job(h):
    event = event_object()
    h.gateway.remote["sub_fixture_alpha"] = subscription_object()
    assert h.post_event(event).status_code == 202
    assert h.post_event(event).json()["status"] == "duplicate"
    assert len(h.app.state.billing.jobs("alpha")["jobs"]) == 1
    assert h.app.state.worker.run_once()
    assert h.post_event(event).json()["original_status"] == "processed"
    assert not h.app.state.worker.run_once()


def test_old_notification_fetches_current_state_instead_of_old_snapshot(h):
    h.gateway.remote["sub_fixture_alpha"] = subscription_object(status="canceled")
    # Snapshot says active, but Stripe's current state has already been canceled.
    assert h.post_event(event_object(status="active")).status_code == 202
    h.app.state.worker.run_once()
    status = h.app.state.billing.status("alpha")
    assert status["status"] == "canceled" and status["plan_id"] == "free"


@pytest.mark.parametrize(
    "remote_status,plan,http_status",
    [
        ("active", "pro", 200),
        ("trialing", "pro", 200),
        ("past_due", "free", 402),
        ("unpaid", "free", 402),
        ("canceled", "free", 200),
    ],
)
def test_verified_subscription_state_controls_entitlement(h, remote_status, plan, http_status):
    h.gateway.remote["sub_fixture_alpha"] = subscription_object(status=remote_status)
    event_type = "customer.subscription.deleted" if remote_status == "canceled" else "customer.subscription.updated"
    assert h.post_event(event_object(event_type=event_type)).status_code == 202
    h.app.state.worker.run_once()
    assert h.app.state.meter.usage("alpha")["plan"] == plan
    assert h.client.post("/generate", json={}, headers=h.headers()).status_code == http_status


def test_live_events_and_live_api_keys_are_refused(h):
    event = event_object()
    event["livemode"] = True
    assert h.post_event(event).status_code == 400
    with pytest.raises(ValueError, match="sk_test_"):
        Settings(stripe_secret_key="sk_" + "live_not_a_real_key")


def test_unknown_tenant_and_unhandled_event_are_ignored(h):
    event = event_object(tenant="unrelated")
    assert h.post_event(event).json()["status"] == "ignored"
    event = event_object("evt_unhandled", event_type="invoice.created")
    assert h.post_event(event).json()["status"] == "ignored"
    assert not h.app.state.worker.run_once()


def test_signed_malformed_data_is_clean_400(h):
    event = event_object()
    event["data"] = []
    assert h.post_event(event).status_code == 400
    event["data"] = {"object": {"id": "sub_bad", "metadata": {"tenant_id": []}}}
    assert h.post_event(event).status_code == 400


def test_checkout_competing_keys_and_existing_subscription_are_blocked(h):
    assert h.client.post("/billing/checkout", headers=h.headers()).status_code == 200
    assert h.client.post("/billing/checkout", headers=h.headers(key="another")).status_code == 409
    with h.app.state.db.transaction() as conn:
        conn.execute(
            "UPDATE subscriptions SET stripe_subscription_id='sub_existing',status='active' WHERE tenant_id='beta'"
        )
    assert h.client.post("/billing/checkout", headers=h.headers("beta")).status_code == 409


def complete_and_cancel_checkout(h):
    original = h.client.post("/billing/checkout", headers=h.headers())
    assert original.status_code == 200
    h.gateway.remote["sub_fixture_alpha"] = subscription_object()
    assert h.post_event(event_object("evt_completed", event_type="checkout.session.completed")).status_code == 202
    assert h.app.state.worker.run_once()
    h.gateway.remote["sub_fixture_alpha"] = subscription_object(status="canceled")
    assert h.post_event(event_object("evt_canceled", event_type="customer.subscription.deleted")).status_code == 202
    assert h.app.state.worker.run_once()
    assert h.app.state.billing.status("alpha")["status"] == "canceled"
    return original


def test_verified_cancellation_allows_new_checkout_and_preserves_old_replay(h):
    original = complete_and_cancel_checkout(h)
    new = h.client.post("/billing/checkout", headers=h.headers(key="resubscribe"))
    assert new.status_code == 200
    assert new.json()["session_id"] != original.json()["session_id"]
    replay = h.client.post("/billing/checkout", headers=h.headers())
    assert replay.status_code == 200 and replay.json() == original.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert len(h.gateway.checkouts) == 2


def test_repeated_canceled_notification_does_not_unlock_new_checkout(h):
    complete_and_cancel_checkout(h)
    assert h.client.post("/billing/checkout", headers=h.headers(key="resubscribe")).status_code == 200
    assert (
        h.post_event(event_object("evt_late_canceled", event_type="customer.subscription.deleted")).status_code == 202
    )
    assert h.app.state.worker.run_once()
    competing = h.client.post("/billing/checkout", headers=h.headers(key="competing"))
    assert competing.status_code == 409
    assert competing.json()["error"]["code"] == "checkout_in_progress"
    assert len(h.gateway.checkouts) == 2


def test_missing_stripe_config_returns_clean_503(tmp_path):
    from fastapi.testclient import TestClient

    from app.cli import seed

    settings = Settings(database_path=str(tmp_path / "offline.db"), worker_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        seed(app.state.db)
        import json

        key = json.loads((tmp_path / "demo-credentials.json").read_text())["demo-alpha"]["api_key"]
        assert client.post("/billing/checkout", headers={"X-API-Key": key, "Idempotency-Key": "a"}).status_code == 503
        assert client.post("/webhooks/stripe", json={}).status_code == 503


def test_worker_retries_then_recovers_and_survives_restart(h):
    h.gateway.remote["sub_fixture_alpha"] = subscription_object()
    h.gateway.failures = 1
    h.post_event(event_object())
    assert h.app.state.worker.run_once()
    job = h.app.state.billing.jobs("alpha")["jobs"][0]
    assert job["status"] == "pending" and job["attempts"] == 1
    with h.app.state.db.transaction() as conn:
        conn.execute("UPDATE jobs SET available_at=0")
    restarted = Worker(h.app.state.db, h.settings, h.gateway)
    assert restarted.run_once()
    job = h.app.state.billing.jobs("alpha")["jobs"][0]
    assert job["status"] == "done" and job["attempts"] == 2
    assert h.app.state.meter.usage("alpha")["plan"] == "pro"


def test_terminal_failure_has_durable_alert_without_secret_logs(h, caplog):
    h.gateway.failures = 3
    h.post_event(event_object())
    for _ in range(3):
        assert h.app.state.worker.run_once()
        with h.app.state.db.transaction() as conn:
            conn.execute("UPDATE jobs SET available_at=0")
    result = h.app.state.billing.jobs("alpha")
    assert result["jobs"][0]["status"] == "dead"
    assert result["jobs"][0]["attempts"] == 3
    assert len(result["alerts"]) == 1
    assert "billing_sync_failed" in caplog.text
    assert "sensitive provider message" not in caplog.text
    assert h.app.state.billing.jobs("beta") == {"jobs": [], "alerts": []}


def test_per_tenant_lease_and_stale_worker_fencing(h):
    h.gateway.remote["sub_fixture_alpha"] = subscription_object()
    h.post_event(event_object("evt_one"))
    h.post_event(event_object("evt_two"))
    worker = h.app.state.worker
    claimed = worker.claim()
    assert claimed
    assert worker.claim() is None  # Another job cannot sync this tenant concurrently.
    with h.app.state.db.transaction() as conn:
        conn.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (claimed["id"],))
    replacement = worker.claim()
    assert replacement["lease_token"] != claimed["lease_token"]
    assert replacement["id"] == claimed["id"] and replacement["attempts"] == 2


def test_wrong_tenant_or_price_never_grants_pro(h):
    remote = subscription_object(tenant="beta", price="price_unexpected")
    remote["id"] = "sub_fixture_alpha"
    h.gateway.remote["sub_fixture_alpha"] = copy.deepcopy(remote)
    h.post_event(event_object())
    h.app.state.worker.run_once()
    assert h.app.state.meter.usage("alpha")["plan"] == "free"
    assert h.app.state.billing.jobs("alpha")["jobs"][0]["last_error"] == "ValueError"
