"""Probe a running local API without printing credentials or changing subscription state."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--credentials", default="data/demo-credentials.json")
    args = parser.parse_args()
    credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    key = credentials["demo-alpha"]["api_key"]

    def call(method, path, body=None, idempotency_key=None):
        headers = {"X-API-Key": key, "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = json.dumps(body).encode() if body is not None else None
        req = Request(args.base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=15) as result:
                return result.status, json.load(result)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    status, health = call("GET", "/health")
    assert status == 200 and health["status"] == "ok"
    _, before = call("GET", "/usage")
    payload = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 500, "reasoning_tokens": 200}
    request_key = "smoke-" + str(uuid4())
    first_status, first = call("POST", "/generate", payload, request_key)
    second_status, second = call("POST", "/generate", payload, request_key)
    assert first_status == second_status == 200, (first_status, first)
    assert first == second
    _, after = call("GET", "/usage")
    assert after["events"] == before["events"] + 1
    assert first["cost"]["total_micro_usd"] == 6600
    print(
        json.dumps(
            {
                "health": health,
                "first_status": first_status,
                "retry_status": second_status,
                "identical_response": first == second,
                "new_events": after["events"] - before["events"],
                "request_cost_micro_usd": first["cost"]["total_micro_usd"],
                "request_cost_usd": first["cost"]["total_usd"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
