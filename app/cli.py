import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

from app.config import Settings
from app.db import Database, hash_key
from app.metering import utc_now


def seed(db):
    db.migrate()
    credentials_path = Path(db.path).parent / "demo-credentials.json"
    credentials = json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
    with db.transaction() as conn:
        for tenant_id, name in (("demo-alpha", "Alpha Studio"), ("demo-beta", "Beta Labs")):
            key = credentials.get(tenant_id, {}).get("api_key")
            existing = conn.execute("SELECT api_key_hash FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            if existing:
                if not key or hash_key(key) != existing["api_key_hash"]:
                    raise RuntimeError(
                        "Seed credentials missing or mismatched. Restore the ignored credential file; seed never silently rotates keys."
                    )
                continue
            key = "tenant_" + secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO tenants VALUES (?,?,?,?)", (tenant_id, name, hash_key(key), utc_now().isoformat())
            )
            conn.execute(
                "INSERT INTO subscriptions(tenant_id,updated_at) VALUES (?,?)", (tenant_id, utc_now().isoformat())
            )
            credentials[tenant_id] = {"name": name, "api_key": key}
        # Save before commit; if the DB fails, running seed again safely reuses/repairs local demo entries.
        temporary = credentials_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(credentials_path)
    print(f"Seeded two Free tenants. Credentials saved locally to {credentials_path}")
    print("Keep this ignored file private; credentials are not printed or stored in plaintext in the database.")


def main():
    parser = argparse.ArgumentParser(description="Capstone development commands")
    parser.add_argument("command", choices=["run", "migrate", "seed", "test", "worker", "retry-job"])
    parser.add_argument("--once", action="store_true", help="Process at most one background job")
    parser.add_argument("--job-id", help="Failed job ID to explicitly requeue")
    args = parser.parse_args()
    if args.command == "test":
        raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q"]))
    settings = Settings.from_env()
    db = Database(settings.database_path)
    if args.command == "run":
        import uvicorn

        uvicorn.run("app.main:app", host=settings.host, port=settings.port, access_log=False)
    elif args.command == "seed":
        seed(db)
    elif args.command == "migrate":
        db.migrate()
        print("Migrations applied.")
    elif args.command == "worker":
        from app.stripe_gateway import StripeGateway
        from app.worker import Worker

        db.migrate()
        logging.basicConfig(level=logging.INFO)
        worker = Worker(db, settings, StripeGateway(settings))
        if args.once:
            print("Processed one job." if worker.run_once() else "No job ready.")
        else:
            try:
                worker._loop()
            except KeyboardInterrupt:
                worker.stop()
    elif args.command == "retry-job":
        if not args.job_id:
            parser.error("retry-job requires --job-id")
        with db.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=? AND status='dead'", (args.job_id,)).fetchone()
            if not job:
                parser.error("No failed job with this ID")
            conn.execute(
                "UPDATE jobs SET status='pending',attempts=0,available_at=0,lease_until=0,lease_token=NULL WHERE id=?",
                (args.job_id,),
            )
            conn.execute("UPDATE webhook_events SET status='queued' WHERE id=?", (job["event_id"],))
            conn.execute("DELETE FROM alerts WHERE job_id=?", (args.job_id,))
        print("Failed job requeued. The worker will fetch current Stripe state.")


if __name__ == "__main__":
    main()
