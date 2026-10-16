import json
import secrets
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import Settings
from app.errors import DomainError
from app.stripe_gateway import StripeGateway
from tests.conftest import subscription_object


def test_real_sdk_checkout_request_and_subscription_retrieval(monkeypatch):
    """Exercise the real adapter + SDK; intercept HTTP before any network traffic."""
    captured = []

    def send(client, request, **kwargs):
        captured.append(request)
        if request.method == "POST":
            obj = {
                "id": "cs_test_sdk",
                "object": "checkout.session",
                "livemode": False,
                "url": "https://checkout.stripe.com/test-fixture",
                "expires_at": 9999999999,
            }
        else:
            obj = subscription_object()
        return httpx.Response(
            200,
            request=request,
            content=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json", "Request-Id": "req_fixture"},
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    settings = Settings(stripe_secret_key="sk_test_" + secrets.token_hex(24), stripe_pro_price_id="price_fixture_pro")
    gateway = StripeGateway(settings)
    session = gateway.checkout("alpha", "cus_fixture_alpha", "price_fixture_pro", "durable-request-id")
    assert session["id"] == "cs_test_sdk"
    assert captured[0].url.path == "/v1/checkout/sessions"
    assert captured[0].headers["Idempotency-Key"] == "capstone-checkout-durable-request-id"
    params = parse_qs(captured[0].content.decode())
    assert params["mode"] == ["subscription"]
    assert params["subscription_data[metadata][tenant_id]"] == ["alpha"]
    assert params["line_items[0][price]"] == ["price_fixture_pro"]
    assert params["customer"] == ["cus_fixture_alpha"]
    assert gateway.subscription("sub_fixture_alpha")["status"] == "active"
    assert captured[1].url.path == "/v1/subscriptions/sub_fixture_alpha"


def test_provider_errors_are_sanitized(monkeypatch):
    def send(client, request, **kwargs):
        return httpx.Response(
            500, request=request, json={"error": {"type": "api_error", "message": "private provider detail"}}
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    gateway = StripeGateway(
        Settings(stripe_secret_key="sk_test_" + secrets.token_hex(24), stripe_pro_price_id="price_fixture")
    )
    with pytest.raises(DomainError) as error:
        gateway.checkout("alpha", None, "price_fixture", "id")
    assert error.value.status == 502
    assert "private provider detail" not in error.value.message
