import json
import sqlite3

import pytest

from app.cli import seed
from app.db import Database
from app.metering import MeterService
from app.schemas import GenerateRequest


def test_migrations_and_seed_are_repeatable(tmp_path):
    db = Database(str(tmp_path / "seed.db"))
    seed(db)
    first = json.loads((tmp_path / "demo-credentials.json").read_text())
    seed(db)
    db.migrate()
    assert first == json.loads((tmp_path / "demo-credentials.json").read_text())
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 2
        stored = conn.execute("SELECT api_key_hash FROM tenants WHERE id='demo-alpha'").fetchone()[0]
        assert stored != first["demo-alpha"]["api_key"]


def test_usage_persists_across_new_service_instances(h):
    original, _ = h.app.state.meter.record("alpha", "persist", GenerateRequest(input_tokens=77))
    service = MeterService(Database(h.settings.database_path))
    replay, duplicate = service.record("alpha", "persist", GenerateRequest(input_tokens=77))
    assert original == replay and duplicate
    assert service.usage("alpha")["tokens"]["used"] == 77


def test_database_itself_prevents_duplicate_usage(h):
    h.app.state.meter.record("alpha", "constraint", GenerateRequest())
    with pytest.raises(sqlite3.IntegrityError):
        with h.app.state.db.transaction() as conn:
            conn.execute(
                "INSERT INTO usage_events SELECT 'another-id',tenant_id,idempotency_key,request_hash,period,"
                "api_calls,input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,total_tokens,"
                "api_cost_micro_usd,token_cost_micro_usd,pricing_version,response_json,created_at FROM usage_events"
            )
    assert h.app.state.meter.usage("alpha")["events"] == 1
