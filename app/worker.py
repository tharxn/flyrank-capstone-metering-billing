import json
import logging
import threading
import time
from uuid import uuid4

from app.metering import utc_now

logger = logging.getLogger("billing.worker")
LEASE_SECONDS = 60


class Worker:
    """Durable jobs with per-tenant leases and fencing against stale workers."""

    def __init__(self, db, settings, gateway):
        self.db, self.settings, self.gateway = db, settings, gateway
        self.stop_event = threading.Event()
        self.thread = None

    def _dead(self, conn, job, error):
        conn.execute("UPDATE jobs SET status='dead',last_error=?,lease_until=0 WHERE id=?", (error, job["id"]))
        conn.execute("UPDATE webhook_events SET status='failed' WHERE id=?", (job["event_id"],))
        conn.execute(
            "INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?)",
            (
                str(uuid4()),
                job["tenant_id"],
                job["id"],
                "Stripe sync failed after retry limit. Operator action required.",
                utc_now().isoformat(),
            ),
        )
        # Never log exception messages: provider exceptions can contain sensitive fields.
        logger.error("billing_sync_failed job_id=%s error_type=%s", job["id"], error)

    def claim(self):
        now = int(time.time())
        with self.db.transaction() as conn:
            exhausted = conn.execute(
                "SELECT * FROM jobs WHERE status='running' AND lease_until<=? AND attempts>=?",
                (now, self.settings.job_max_attempts),
            ).fetchall()
            for job in exhausted:
                self._dead(conn, job, "LeaseExpired")
            job = conn.execute(
                "SELECT j.* FROM jobs j WHERE ((j.status='pending' AND j.available_at<=?) "
                "OR (j.status='running' AND j.lease_until<=?)) AND j.attempts<? "
                "AND NOT EXISTS (SELECT 1 FROM jobs active WHERE active.tenant_id=j.tenant_id "
                "AND active.id!=j.id AND active.status='running' AND active.lease_until>?) "
                "ORDER BY j.created_at, j.id LIMIT 1",
                (now, now, self.settings.job_max_attempts, now),
            ).fetchone()
            if not job:
                return None
            token = str(uuid4())
            conn.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,lease_until=?,lease_token=? WHERE id=?",
                (now + LEASE_SECONDS, token, job["id"]),
            )
            return {**dict(job), "attempts": job["attempts"] + 1, "lease_token": token}

    def _apply(self, conn, job, remote):
        sub_id = json.loads(job["payload_json"])["subscription_id"]
        if remote.get("livemode") is not False or remote.get("id") != sub_id:
            raise ValueError("invalid_subscription")
        if (remote.get("metadata") or {}).get("tenant_id") != job["tenant_id"]:
            raise ValueError("tenant_mismatch")
        customer = remote.get("customer")
        if not isinstance(customer, str) or not customer.startswith("cus_"):
            raise ValueError("invalid_customer")
        current = conn.execute("SELECT * FROM subscriptions WHERE tenant_id=?", (job["tenant_id"],)).fetchone()
        # Never allow an old canceled subscription to replace a newer subscription.
        if current["stripe_subscription_id"] and current["stripe_subscription_id"] != sub_id:
            if current["status"] != "canceled" or remote.get("status") == "canceled":
                return
        if current["stripe_customer_id"] and current["stripe_customer_id"] != customer:
            raise ValueError("customer_mismatch")
        status = remote.get("status")
        if status not in {
            "active",
            "trialing",
            "past_due",
            "unpaid",
            "canceled",
            "incomplete",
            "incomplete_expired",
            "paused",
        }:
            raise ValueError("unknown_subscription_status")
        items = remote.get("items", {}).get("data", [])
        valid_price = len(items) == 1 and items[0].get("price", {}).get("id") == self.settings.stripe_pro_price_id
        if status in {"active", "trialing"} and not valid_price:
            raise ValueError("unexpected_price")
        if status == "canceled" and current["status"] != "canceled":
            # Release the old Checkout reservation on the verified transition only.
            # Later canceled notifications must not unlock a new pending Checkout.
            # Stored responses remain intact, so old successful keys still replay.
            conn.execute("UPDATE checkout_requests SET expires_at=0 WHERE tenant_id=?", (job["tenant_id"],))
        plan = "pro" if status in {"active", "trialing"} else "free"
        conn.execute(
            "UPDATE subscriptions SET plan_id=?,status=?,stripe_customer_id=?,stripe_subscription_id=?,"
            "last_event_id=?,updated_at=? WHERE tenant_id=?",
            (plan, status, customer, sub_id, job["event_id"], utc_now().isoformat(), job["tenant_id"]),
        )

    def run_once(self):
        job = self.claim()
        if not job:
            return False
        try:
            # Fetch current state instead of applying an old event snapshot.
            remote = self.gateway.subscription(json.loads(job["payload_json"])["subscription_id"])
            with self.db.transaction() as conn:
                owner = conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if owner["lease_token"] != job["lease_token"] or owner["status"] != "running":
                    return True
                # An expired worker must not commit even if no replacement claimed yet.
                if owner["lease_until"] <= int(time.time()):
                    return True
                self._apply(conn, job, remote)
                conn.execute("UPDATE jobs SET status='done',last_error=NULL,lease_until=0 WHERE id=?", (job["id"],))
                conn.execute("UPDATE webhook_events SET status='processed' WHERE id=?", (job["event_id"],))
        except Exception as exc:
            error = type(exc).__name__
            with self.db.transaction() as conn:
                owner = conn.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if owner["lease_token"] != job["lease_token"] or owner["status"] != "running":
                    return True
                if job["attempts"] >= self.settings.job_max_attempts:
                    self._dead(conn, job, error)
                else:
                    delay = self.settings.job_retry_seconds * (2 ** (job["attempts"] - 1))
                    conn.execute(
                        "UPDATE jobs SET status='pending',available_at=?,lease_until=0,last_error=? WHERE id=?",
                        (int(time.time()) + delay, error, job["id"]),
                    )
        return True

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                did_work = self.run_once()
            except Exception as exc:
                logger.error("billing_worker_error error_type=%s", type(exc).__name__)
                did_work = False
            if not did_work:
                self.stop_event.wait(self.settings.worker_poll_seconds)

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="stripe-sync", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
