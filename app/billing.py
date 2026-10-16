import json
import time
from uuid import uuid4

from app.errors import DomainError
from app.metering import subscription, utc_now

HANDLED_EVENTS = {"checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted"}


class BillingService:
    def __init__(self, db, settings, gateway):
        self.db, self.settings, self.gateway = db, settings, gateway

    def checkout(self, tenant_id, key):
        self.gateway.require_checkout()
        now = int(time.time())
        with self.db.transaction() as conn:
            previous = conn.execute(
                "SELECT * FROM checkout_requests WHERE tenant_id=? AND idempotency_key=?", (tenant_id, key)
            ).fetchone()
            if previous and previous["response_json"]:
                return json.loads(previous["response_json"]), True
            if previous and previous["expires_at"] <= now:
                raise DomainError(409, "checkout_expired", "This Checkout attempt expired. Use a new idempotency key.")
            current = subscription(conn, tenant_id)
            if current["stripe_subscription_id"] and current["status"] != "canceled":
                raise DomainError(
                    409, "subscription_exists", "An existing subscription must be managed in Stripe test mode."
                )
            if not previous:
                other = conn.execute(
                    "SELECT id FROM checkout_requests WHERE tenant_id=? AND expires_at>? LIMIT 1", (tenant_id, now)
                ).fetchone()
                if other:
                    raise DomainError(
                        409, "checkout_in_progress", "Retry the existing Checkout request with its original key."
                    )
                request_id = str(uuid4())
                conn.execute(
                    "INSERT INTO checkout_requests(id,tenant_id,idempotency_key,price_id,expires_at,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (request_id, tenant_id, key, self.settings.stripe_pro_price_id, now + 86400, utc_now().isoformat()),
                )
                price_id = self.settings.stripe_pro_price_id
            else:
                request_id, price_id = previous["id"], previous["price_id"]
            customer_id = current["stripe_customer_id"]
        # A crash before the following local commit is safe: retry uses the same persisted Stripe key.
        session = self.gateway.checkout(tenant_id, customer_id, price_id, request_id)
        response = {
            "session_id": session["id"],
            "url": session["url"],
            "expires_at": session["expires_at"],
            "test_mode": True,
        }
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE checkout_requests SET stripe_session_id=?,response_json=?,expires_at=? WHERE id=?",
                (session["id"], json.dumps(response), session["expires_at"], request_id),
            )
        return response, False

    def ingest(self, event):
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("id"), str)
            or not isinstance(event.get("type"), str)
        ):
            raise DomainError(400, "invalid_event", "Expected a Stripe event with id and type.")
        if event.get("livemode") is not False:
            raise DomainError(400, "live_mode_refused", "Only Stripe test-mode events are accepted.")
        event_id, event_type = event["id"], event["type"]
        with self.db.transaction() as conn:
            previous = conn.execute("SELECT status FROM webhook_events WHERE id=?", (event_id,)).fetchone()
            if previous:
                return {"event_id": event_id, "status": "duplicate", "original_status": previous["status"]}, 200
            data = event.get("data")
            if not isinstance(data, dict):
                raise DomainError(400, "invalid_event", "Expected Stripe event data.")
            obj = data.get("object", {})
            if not isinstance(obj, dict):
                raise DomainError(400, "invalid_event", "Expected a Stripe event object.")
            if event_type in HANDLED_EVENTS:
                sub_id = obj.get("subscription") if event_type == "checkout.session.completed" else obj.get("id")
                if isinstance(sub_id, dict):
                    sub_id = sub_id.get("id")
                if not isinstance(sub_id, str) or not sub_id.startswith("sub_"):
                    raise DomainError(400, "invalid_event", "Expected a subscription ID.")
                metadata = obj.get("metadata") or {}
                tenant_id = metadata.get("tenant_id") if isinstance(metadata, dict) else None
                if tenant_id is not None and not isinstance(tenant_id, str):
                    raise DomainError(400, "invalid_event", "Expected a tenant identifier string.")
                known = conn.execute(
                    "SELECT tenant_id FROM subscriptions WHERE stripe_subscription_id=?", (sub_id,)
                ).fetchone()
                if known:
                    tenant_id = known["tenant_id"]
                tenant = conn.execute("SELECT id FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            else:
                tenant = None
            status = "queued" if tenant else "ignored"
            conn.execute(
                "INSERT INTO webhook_events VALUES (?,?,?,?,?)",
                (event_id, event_type, tenant["id"] if tenant else None, status, utc_now().isoformat()),
            )
            if tenant:
                job_id = str(uuid4())
                # Retain only identifiers required to sync, not full payment/customer payloads.
                payload = {"subscription_id": sub_id, "event_type": event_type}
                conn.execute(
                    "INSERT INTO jobs(id,event_id,tenant_id,payload_json,available_at,created_at) VALUES (?,?,?,?,?,?)",
                    (job_id, event_id, tenant["id"], json.dumps(payload), int(time.time()), utc_now().isoformat()),
                )
                return {"event_id": event_id, "status": "queued", "job_id": job_id}, 202
            return {"event_id": event_id, "status": "ignored"}, 200

    def status(self, tenant_id):
        with self.db.connect() as conn:
            row = subscription(conn, tenant_id)
            return {
                k: row[k]
                for k in (
                    "tenant_id",
                    "plan_id",
                    "status",
                    "stripe_customer_id",
                    "stripe_subscription_id",
                    "updated_at",
                )
            }

    def jobs(self, tenant_id):
        with self.db.connect() as conn:
            jobs = conn.execute(
                "SELECT id,event_id,status,attempts,last_error FROM jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT 50",
                (tenant_id,),
            ).fetchall()
            alerts = conn.execute(
                "SELECT job_id,message,created_at FROM alerts WHERE tenant_id=? ORDER BY created_at DESC LIMIT 50",
                (tenant_id,),
            ).fetchall()
        return {"jobs": [dict(row) for row in jobs], "alerts": [dict(row) for row in alerts]}
