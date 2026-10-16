import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PRICING_VERSION = "simulated-usd-v1"
API_CALL_MICRO_USD = 1_000
INPUT_TOKEN_MICRO_USD = 2
CACHED_TOKEN_MICRO_USD = 1
OUTPUT_TOKEN_MICRO_USD = 8
MICRO_USD_PER_USD = 1_000_000


@dataclass(frozen=True)
class Settings:
    database_path: str = "data/billing.db"
    host: str = "127.0.0.1"
    port: int = 8000
    worker_enabled: bool = True
    worker_poll_seconds: float = 1
    job_max_attempts: int = 3
    job_retry_seconds: int = 2
    stripe_secret_key: str = field(default="", repr=False)
    stripe_webhook_secret: str = field(default="", repr=False)
    stripe_pro_price_id: str = ""
    checkout_success_url: str = "http://localhost:8000/billing/return?result=success"
    checkout_cancel_url: str = "http://localhost:8000/billing/return?result=cancelled"

    def __post_init__(self):
        if self.stripe_secret_key and not re.fullmatch(r"(?:sk|rk|rkcs)_test_[A-Za-z0-9]+", self.stripe_secret_key):
            raise ValueError("Only Stripe test secret keys (sk_test_, rk_test_, rkcs_test_) are accepted")
        if self.stripe_webhook_secret and not self.stripe_webhook_secret.startswith("whsec_"):
            raise ValueError("Stripe webhook secret must start with whsec_")
        if not 1 <= self.job_max_attempts <= 10 or self.job_retry_seconds < 1:
            raise ValueError("Job attempts must be 1..10 and retry seconds >= 1")
        if self.worker_poll_seconds < 0.1:
            raise ValueError("Worker polling must be at least 0.1 seconds")

    @classmethod
    def from_env(cls):
        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
        return cls(
            database_path=os.getenv("DATABASE_PATH", "data/billing.db"),
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8000")),
            worker_enabled=os.getenv("WORKER_ENABLED", "true").lower() == "true",
            worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1")),
            job_max_attempts=int(os.getenv("JOB_MAX_ATTEMPTS", "3")),
            job_retry_seconds=int(os.getenv("JOB_RETRY_SECONDS", "2")),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", ""),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            stripe_pro_price_id=os.getenv("STRIPE_PRO_PRICE_ID", ""),
            checkout_success_url=os.getenv("CHECKOUT_SUCCESS_URL", cls.checkout_success_url),
            checkout_cancel_url=os.getenv("CHECKOUT_CANCEL_URL", cls.checkout_cancel_url),
        )
