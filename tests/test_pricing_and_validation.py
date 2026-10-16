import pytest

from app.metering import calculate_cost, usd_string
from app.schemas import GenerateRequest


def test_cached_and_reasoning_tokens_are_inclusive_and_integer(h):
    response = h.client.post(
        "/generate",
        headers=h.headers(),
        json={
            "input_tokens": 1000,
            "cached_input_tokens": 400,
            "output_tokens": 500,
            "reasoning_tokens": 200,
        },
    ).json()
    assert response["usage"]["total_tokens"] == 1500
    # 600*2 + 400*1 + 500*8 + 1*1000 = 6600; reasoning is already in 500.
    assert response["cost"]["total_micro_usd"] == 6600
    assert response["cost"]["total_usd"] == "0.006600"
    assert all(
        type(response["cost"][key]) is int
        for key in ("input_micro_usd", "cached_input_micro_usd", "output_micro_usd", "total_micro_usd")
    )
    assert h.app.state.meter.usage("alpha")["cost"]["total_usd"] == "0.006600"


def test_cached_input_is_cheaper_and_reasoning_does_not_add_cost():
    fresh = calculate_cost(GenerateRequest(input_tokens=1000, output_tokens=500))
    cached = calculate_cost(
        GenerateRequest(input_tokens=1000, cached_input_tokens=1000, output_tokens=500, reasoning_tokens=500)
    )
    assert sum(fresh.values()) == 7000
    assert sum(cached.values()) == 6000
    assert fresh["output_micro_usd"] == cached["output_micro_usd"] == 4000
    assert usd_string(1234567890123456789) == "1234567890123.456789"


@pytest.mark.parametrize(
    "body",
    [
        {"input_tokens": -1},
        {"input_tokens": 1.5},
        {"input_tokens": True},
        {"input_tokens": "10"},
        {"input_tokens": 10_000_001},
        {"cached_input_tokens": 1},
        {"reasoning_tokens": 1},
        {"tenant_id": "beta"},
        {"output_tokens": None},
        [],
    ],
)
def test_invalid_token_input_is_clean_422_without_usage(h, body):
    assert h.client.post("/generate", json=body, headers=h.headers()).status_code == 422
    assert h.app.state.meter.usage("alpha")["events"] == 0


def test_missing_invalid_and_oversized_keys(h):
    assert h.client.get("/usage").status_code == 401
    assert h.client.get("/usage", headers={"X-API-Key": "wrong"}).status_code == 401
    assert h.client.post("/generate", json={}, headers={"X-API-Key": h.keys["alpha"]}).status_code == 422
    assert h.client.post("/generate", json={}, headers=h.headers(key="a" * 129)).status_code == 422
    assert h.client.post("/generate", json={}, headers=h.headers(key="has space")).status_code == 422


def test_tenant_usage_and_idempotency_keys_are_isolated(h):
    a = h.client.post("/generate", json={"input_tokens": 10}, headers=h.headers())
    b = h.client.post("/generate", json={"input_tokens": 20}, headers=h.headers("beta"))
    assert a.status_code == b.status_code == 200
    assert a.json()["event_id"] != b.json()["event_id"]
    assert h.client.get("/usage", headers=h.headers()).json()["tokens"]["used"] == 10
    assert h.client.get("/usage", headers=h.headers("beta")).json()["tokens"]["used"] == 20
