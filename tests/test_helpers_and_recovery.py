import hashlib
import hmac
import json
import sys
from io import BytesIO

import pytest

from scripts import replay_webhook
from tests.conftest import event_object, subscription_object


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_saved_event_replay_preserves_id_and_produces_valid_signature(h, tmp_path, monkeypatch, encoding):
    event = event_object()
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event), encoding=encoding)
    monkeypatch.setattr(replay_webhook.Settings, "from_env", lambda: h.settings)
    monkeypatch.setattr(sys, "argv", ["replay_webhook", str(event_file)])

    def urlopen(request, timeout):
        timestamp, signature = request.get_header("Stripe-signature").split(",")
        expected = hmac.new(
            h.settings.stripe_webhook_secret.encode(),
            timestamp.removeprefix("t=").encode() + b"." + request.data,
            hashlib.sha256,
        ).hexdigest()
        assert signature == "v1=" + expected
        assert json.loads(request.data)["id"] == event["id"]
        response = BytesIO(b'{"status":"duplicate"}')
        response.status = 200
        return response

    monkeypatch.setattr(replay_webhook, "urlopen", urlopen)
    replay_webhook.main()


def test_stale_worker_cannot_commit_after_replacement_takes_lease(h, monkeypatch):
    h.post_event(event_object())

    def stolen_lease(subscription_id):
        with h.app.state.db.transaction() as conn:
            conn.execute("UPDATE jobs SET lease_token='replacement-owner'")
        return subscription_object()

    monkeypatch.setattr(h.gateway, "subscription", stolen_lease)
    assert h.app.state.worker.run_once()
    assert h.app.state.meter.usage("alpha")["plan"] == "free"
    assert h.app.state.billing.jobs("alpha")["jobs"][0]["status"] == "running"


def test_crashed_final_attempt_becomes_dead_with_alert(h):
    h.post_event(event_object())
    with h.app.state.db.transaction() as conn:
        conn.execute("UPDATE jobs SET status='running',attempts=3,lease_until=0")
    assert not h.app.state.worker.run_once()
    assert h.app.state.billing.jobs("alpha")["jobs"][0]["status"] == "dead"
    assert len(h.app.state.billing.jobs("alpha")["alerts"]) == 1
