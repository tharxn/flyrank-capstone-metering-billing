import stripe

from app.errors import DomainError


class StripeGateway:
    """Only boundary that talks to Stripe; dependency-injected in offline tests."""

    def __init__(self, settings):
        self.settings = settings
        self.client = None
        if settings.stripe_secret_key:
            self.client = stripe.StripeClient(
                settings.stripe_secret_key,
                max_network_retries=0,  # Durable jobs own retries; Checkout callers retry with the same key.
                http_client=stripe.HTTPXClient(timeout=10, allow_sync_methods=True),
            )

    def require_checkout(self):
        if not self.client or not self.settings.stripe_pro_price_id:
            raise DomainError(
                503, "stripe_not_configured", "Configure a Stripe test key and Pro recurring Price locally."
            )

    def verify(self, payload: bytes, signature: str):
        if not self.settings.stripe_webhook_secret:
            raise DomainError(503, "webhook_not_configured", "Configure STRIPE_WEBHOOK_SECRET locally.")
        try:
            return stripe.Webhook.construct_event(
                payload, signature, self.settings.stripe_webhook_secret, tolerance=300
            )
        except (ValueError, KeyError, TypeError, AttributeError, stripe.SignatureVerificationError):
            raise DomainError(400, "invalid_signature", "Invalid Stripe webhook signature or payload.") from None

    def checkout(self, tenant_id, customer_id, price_id, request_id):
        self.require_checkout()
        params = {
            "mode": "subscription",
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": tenant_id,
            "metadata": {"tenant_id": tenant_id},
            "subscription_data": {"metadata": {"tenant_id": tenant_id}},
            "success_url": self.settings.checkout_success_url,
            "cancel_url": self.settings.checkout_cancel_url,
        }
        if customer_id:
            params["customer"] = customer_id
        try:
            session = self.client.v1.checkout.sessions.create(
                params, options={"idempotency_key": f"capstone-checkout-{request_id}"}
            )
        except stripe.StripeError:
            raise DomainError(
                502, "stripe_unavailable", "Stripe Checkout failed. Retry using the same idempotency key."
            ) from None
        if session.get("livemode") is not False:
            raise DomainError(502, "live_mode_refused", "Refused a non-test Checkout session.")
        return session

    def subscription(self, subscription_id):
        if not self.client:
            raise RuntimeError("stripe_not_configured")
        return self.client.v1.subscriptions.retrieve(subscription_id)
