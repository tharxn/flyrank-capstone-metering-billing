import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.billing import BillingService
from app.config import Settings
from app.db import Database, hash_key
from app.errors import DomainError
from app.metering import MeterService
from app.schemas import GenerateRequest
from app.stripe_gateway import StripeGateway
from app.worker import Worker


def create_app(settings=None, *, gateway=None, clock=None):
    settings = settings or Settings.from_env()
    db = Database(settings.database_path)
    gateway = gateway or StripeGateway(settings)
    meter = MeterService(db, **({"clock": clock} if clock else {}))
    billing = BillingService(db, settings, gateway)
    worker = Worker(db, settings, gateway)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        if settings.worker_enabled:
            worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(
        title="Usage Metering & Billing Engine",
        version="1.0.0",
        lifespan=lifespan,
        description="Tenant-isolated metering, exact integer money, and Stripe test-mode subscriptions. "
        "Authenticate via X-API-Key. Seed local credentials with python -m app.cli seed.",
    )
    app.state.db, app.state.meter, app.state.billing, app.state.worker = db, meter, billing, worker

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            headers=exc.headers,
            content={"error": {"code": exc.code, "message": exc.message, **exc.details}},
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def database_unavailable(request, exc):
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={
                "error": {"code": "database_busy", "message": "Database unavailable. Retry the same request key."}
            },
        )

    def tenant(x_api_key: str = Header(default="", alias="X-API-Key")):
        if not x_api_key or len(x_api_key) > 256:
            raise DomainError(401, "unauthorized", "A valid X-API-Key is required.")
        with db.connect() as conn:
            row = conn.execute("SELECT id FROM tenants WHERE api_key_hash=?", (hash_key(x_api_key),)).fetchone()
        if not row:
            raise DomainError(401, "unauthorized", "A valid X-API-Key is required.")
        return row["id"]

    def idempotency_key(key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
        if any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise DomainError(422, "invalid_idempotency_key", "Use 1..128 visible ASCII characters without spaces.")
        return key

    @app.get("/health", tags=["System"])
    def health():
        with db.connect() as conn:
            conn.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
        return {"status": "ok", "database": "sqlite", "stripe_mode": "test"}

    @app.post("/generate", tags=["Metering"])
    def generate(body: GenerateRequest, response: Response, tenant_id=Depends(tenant), key=Depends(idempotency_key)):
        result, replay = meter.record(tenant_id, key, body)
        response.headers["Idempotency-Replayed"] = str(replay).lower()
        return result

    @app.get("/usage", tags=["Metering"])
    def usage(tenant_id=Depends(tenant)):
        return meter.usage(tenant_id)

    @app.post("/billing/checkout", tags=["Billing"])
    def checkout(response: Response, tenant_id=Depends(tenant), key=Depends(idempotency_key)):
        result, replay = billing.checkout(tenant_id, key)
        response.headers["Idempotency-Replayed"] = str(replay).lower()
        return result

    @app.get("/billing/subscription", tags=["Billing"])
    def billing_status(tenant_id=Depends(tenant)):
        return billing.status(tenant_id)

    @app.get("/billing/jobs", tags=["Billing"])
    def jobs(tenant_id=Depends(tenant)):
        return billing.jobs(tenant_id)

    @app.get("/billing/return", response_class=PlainTextResponse, tags=["Billing"])
    def checkout_return():
        return "Checkout returned. Check GET /billing/subscription for verified webhook sync status."

    @app.post("/webhooks/stripe", tags=["Billing"])
    async def webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature")):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1_048_576:
                raise DomainError(413, "payload_too_large", "Webhook payload exceeds 1 MiB.")
            chunks.append(chunk)
        event = gateway.verify(b"".join(chunks), stripe_signature)
        result, status = await run_in_threadpool(billing.ingest, event)
        return JSONResponse(result, status_code=status)

    return app


app = create_app()
