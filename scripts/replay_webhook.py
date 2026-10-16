"""Locally re-sign a saved real test event. Never uses or prints an API secret."""

import argparse
import hashlib
import hmac
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_file")
    parser.add_argument("--url", default="http://localhost:8000/webhooks/stripe")
    args = parser.parse_args()
    secret = Settings.from_env().stripe_webhook_secret
    if not secret:
        parser.error("Configure STRIPE_WEBHOOK_SECRET in .env first")
    payload = Path(args.event_file).read_bytes()
    # Windows PowerShell 5 redirects native CLI output as UTF-16; newer shells use UTF-8.
    encoding = "utf-16" if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    payload = payload.decode(encoding).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    req = Request(
        args.url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={timestamp},v1={digest}",
        },
    )
    try:
        with urlopen(req, timeout=15) as response:
            print(response.status, response.read().decode())
    except HTTPError as exc:
        print(exc.code, exc.read().decode())
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
