CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    api_call_limit INTEGER NOT NULL CHECK(api_call_limit > 0),
    token_limit INTEGER NOT NULL CHECK(token_limit > 0)
);
INSERT INTO plans VALUES ('free', 1000, 100000), ('pro', 10000, 1000000);
CREATE TABLE tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE subscriptions (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    plan_id TEXT NOT NULL DEFAULT 'free' REFERENCES plans(id),
    status TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id TEXT UNIQUE,
    stripe_subscription_id TEXT UNIQUE,
    last_event_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    period TEXT NOT NULL,
    api_calls INTEGER NOT NULL CHECK(api_calls = 1),
    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
    cached_input_tokens INTEGER NOT NULL CHECK(cached_input_tokens BETWEEN 0 AND input_tokens),
    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
    reasoning_tokens INTEGER NOT NULL CHECK(reasoning_tokens BETWEEN 0 AND output_tokens),
    total_tokens INTEGER NOT NULL CHECK(total_tokens = input_tokens + output_tokens),
    api_cost_micro_usd INTEGER NOT NULL CHECK(api_cost_micro_usd >= 0),
    token_cost_micro_usd INTEGER NOT NULL CHECK(token_cost_micro_usd >= 0),
    pricing_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);
CREATE INDEX usage_tenant_period ON usage_events(tenant_id, period);
CREATE TABLE checkout_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL,
    price_id TEXT NOT NULL,
    stripe_session_id TEXT UNIQUE,
    response_json TEXT,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);
CREATE INDEX checkout_tenant ON checkout_requests(tenant_id, expires_at);
CREATE TABLE webhook_events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    tenant_id TEXT REFERENCES tenants(id),
    status TEXT NOT NULL CHECK(status IN ('queued','processed','ignored','failed')),
    received_at TEXT NOT NULL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE REFERENCES webhook_events(id),
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at INTEGER NOT NULL,
    lease_until INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX jobs_ready ON jobs(status, available_at, lease_until);
CREATE INDEX jobs_tenant ON jobs(tenant_id, status, lease_until);
CREATE TABLE alerts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
