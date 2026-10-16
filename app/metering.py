import hashlib
import json
import math
from datetime import UTC, datetime
from uuid import uuid4

from app.config import (
    API_CALL_MICRO_USD,
    CACHED_TOKEN_MICRO_USD,
    INPUT_TOKEN_MICRO_USD,
    OUTPUT_TOKEN_MICRO_USD,
    PRICING_VERSION,
)
from app.errors import DomainError
from app.schemas import GenerateRequest


def utc_now():
    return datetime.now(UTC)


def month_window(now):
    now = now.astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.strftime("%Y-%m"), start, end


def usd_string(micro_usd: int):
    return f"{micro_usd // 1_000_000}.{micro_usd % 1_000_000:06d}"


def calculate_cost(request: GenerateRequest):
    uncached = request.input_tokens - request.cached_input_tokens
    return {
        "api_micro_usd": API_CALL_MICRO_USD,
        "input_micro_usd": uncached * INPUT_TOKEN_MICRO_USD,
        "cached_input_micro_usd": request.cached_input_tokens * CACHED_TOKEN_MICRO_USD,
        "output_micro_usd": request.output_tokens * OUTPUT_TOKEN_MICRO_USD,
    }


def subscription(conn, tenant_id):
    return conn.execute(
        "SELECT s.*, p.api_call_limit, p.token_limit FROM subscriptions s "
        "JOIN plans p ON p.id=s.plan_id WHERE s.tenant_id=?",
        (tenant_id,),
    ).fetchone()


def rollup(conn, tenant_id, period):
    return conn.execute(
        "SELECT COUNT(*) AS events, COALESCE(SUM(api_calls),0) AS api_calls, "
        "COALESCE(SUM(total_tokens),0) AS tokens, "
        "COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens, "
        "COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens, "
        "COALESCE(SUM(api_cost_micro_usd),0) AS api_cost, "
        "COALESCE(SUM(token_cost_micro_usd),0) AS token_cost "
        "FROM usage_events WHERE tenant_id=? AND period=?",
        (tenant_id, period),
    ).fetchone()


class MeterService:
    def __init__(self, db, clock=utc_now):
        self.db = db
        self.clock = clock

    def record(self, tenant_id, key, request: GenerateRequest):
        normalized = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(normalized.encode()).hexdigest()
        now = self.clock()
        period, _, end = month_window(now)
        with self.db.transaction() as conn:
            previous = conn.execute(
                "SELECT request_hash, response_json FROM usage_events WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise DomainError(409, "idempotency_conflict", "This key was used with different token counts.")
                return json.loads(previous["response_json"]), True
            plan = subscription(conn, tenant_id)
            if plan["status"] not in ("free", "active", "trialing", "canceled"):
                raise DomainError(
                    402,
                    "payment_required",
                    "Subscription requires payment before new usage is allowed.",
                    details={"subscription_status": plan["status"]},
                )
            used = rollup(conn, tenant_id, period)
            tokens = request.input_tokens + request.output_tokens
            for meter, quantity, limit in (
                ("api_calls", 1, plan["api_call_limit"]),
                ("tokens", tokens, plan["token_limit"]),
            ):
                if used[meter] + quantity > limit:
                    raise DomainError(
                        429,
                        "quota_exceeded",
                        f"Monthly {meter} quota exceeded. Wait until reset or upgrade.",
                        details={
                            "meter": meter,
                            "used": used[meter],
                            "requested": quantity,
                            "limit": limit,
                            "resets_at": end.isoformat(),
                        },
                        headers={"Retry-After": str(max(1, math.ceil((end - now).total_seconds())))},
                    )
            parts = calculate_cost(request)
            token_cost = sum(v for k, v in parts.items() if k != "api_micro_usd")
            cost = API_CALL_MICRO_USD + token_cost
            event_id = str(uuid4())
            response = {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "period": period,
                "result": "Simulated generation completed.",
                "usage": {"api_calls": 1, **request.model_dump(), "total_tokens": tokens},
                "cost": {
                    "currency": "USD",
                    "pricing_version": PRICING_VERSION,
                    **parts,
                    "total_micro_usd": cost,
                    "total_usd": usd_string(cost),
                },
                "created_at": now.isoformat(),
            }
            conn.execute(
                "INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    tenant_id,
                    key,
                    request_hash,
                    period,
                    1,
                    request.input_tokens,
                    request.cached_input_tokens,
                    request.output_tokens,
                    request.reasoning_tokens,
                    tokens,
                    API_CALL_MICRO_USD,
                    token_cost,
                    PRICING_VERSION,
                    json.dumps(response, separators=(",", ":")),
                    now.isoformat(),
                ),
            )
        return response, False

    def usage(self, tenant_id):
        period, start, end = month_window(self.clock())
        # A single read transaction gives a consistent plan + usage snapshot.
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            plan = subscription(conn, tenant_id)
            used = rollup(conn, tenant_id, period)
        total = used["api_cost"] + used["token_cost"]
        return {
            "tenant_id": tenant_id,
            "period": period,
            "starts_at": start.isoformat(),
            "resets_at": end.isoformat(),
            "plan": plan["plan_id"],
            "subscription_status": plan["status"],
            "events": used["events"],
            "api_calls": {
                "used": used["api_calls"],
                "limit": plan["api_call_limit"],
                "remaining": max(0, plan["api_call_limit"] - used["api_calls"]),
            },
            "tokens": {
                "used": used["tokens"],
                "limit": plan["token_limit"],
                "remaining": max(0, plan["token_limit"] - used["tokens"]),
                **{k: used[k] for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")},
            },
            "cost": {
                "currency": "USD",
                "api_micro_usd": used["api_cost"],
                "tokens_micro_usd": used["token_cost"],
                "total_micro_usd": total,
                "total_usd": usd_string(total),
                "basis": "stored per-event simulated rates",
            },
        }
