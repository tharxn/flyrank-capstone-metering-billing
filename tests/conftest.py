import copy
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import hash_key
from app.main import create_app
from app.metering import utc_now
from app.stripe_gateway import StripeGateway


class FakeStripe(StripeGateway):
    """No external network; signature verification still uses the actual Stripe SDK."""

    def __init__(self, settings):
        super().__init__(settings)
        self.checkouts = {}
        self.remote = {}
        self.failures = 0

    def require_checkout(self):
        pass

    def checkout(self, tenant_id, customer_id, price_id, request_id):
        return self.checkouts.setdefault(
            request_id,
            {
                "id": "cs_test_" + request_id,
                "url": "https://checkout.stripe.com/test-fixture/" + request_id,
                "expires_at": int(time.time()) + 3600,
                "livemode": False,
            },
        )

    def subscription(self, subscription_id):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("sensitive provider message must never be logged")
        return copy.deepcopy(self.remote[subscription_id])


@dataclass
class Harness:
    app: object
    client: TestClient
    gateway: FakeStripe
    settings: Settings
    keys: dict

    def headers(self, tenant="alpha", key="request-1"):
        return {"X-API-Key": self.keys[tenant], "Idempotency-Key": key}

    def post_event(self, event, *, timestamp=None):
        payload = json.dumps(event, separators=(",", ":")).encode()
        timestamp = int(time.time()) if timestamp is None else timestamp
        digest = hmac.new(
            self.settings.stripe_webhook_secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": f"t={timestamp},v1={digest}", "Content-Type": "application/json"},
        )


@pytest.fixture
def h(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "test.db"),
        worker_enabled=False,
        stripe_webhook_secret="whsec_" + secrets.token_hex(24),
        stripe_pro_price_id="price_fixture_pro",
        job_retry_seconds=1,
    )
    gateway = FakeStripe(settings)
    app = create_app(settings, gateway=gateway)
    keys = {name: "tenant_" + secrets.token_urlsafe(24) for name in ("alpha", "beta")}
    with TestClient(app) as client:
        with app.state.db.transaction() as conn:
            for name, key in keys.items():
                conn.execute("INSERT INTO tenants VALUES (?,?,?,?)", (name, name, hash_key(key), utc_now().isoformat()))
                conn.execute(
                    "INSERT INTO subscriptions(tenant_id,updated_at) VALUES (?,?)", (name, utc_now().isoformat())
                )
        yield Harness(app, client, gateway, settings, keys)


def subscription_object(*, tenant="alpha", status="active", sub_id="sub_fixture_alpha", price="price_fixture_pro"):
    return {
        "id": sub_id,
        "object": "subscription",
        "livemode": False,
        "customer": "cus_fixture_" + tenant,
        "metadata": {"tenant_id": tenant},
        "status": status,
        "items": {"data": [{"price": {"id": price}}]},
    }


def event_object(
    event_id="evt_fixture_1", *, event_type="customer.subscription.updated", tenant="alpha", status="active"
):
    obj = subscription_object(tenant=tenant, status=status, sub_id="sub_fixture_" + tenant)
    if event_type == "checkout.session.completed":
        obj = {
            "id": "cs_test_fixture",
            "object": "checkout.session",
            "mode": "subscription",
            "subscription": "sub_fixture_" + tenant,
            "metadata": {"tenant_id": tenant},
        }
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "livemode": False,
        "created": int(time.time()),
        "data": {"object": obj},
    }
