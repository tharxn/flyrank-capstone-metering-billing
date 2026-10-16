import secrets

import pytest

from app.config import Settings


@pytest.mark.parametrize("prefix", ["sk_test_", "rk_test_", "rkcs_test_"])
def test_standard_restricted_and_temporary_test_secret_keys_are_accepted(prefix):
    key = prefix + secrets.token_hex(24)
    settings = Settings(stripe_secret_key=key)
    assert settings.stripe_secret_key == key
    assert key not in repr(settings)


@pytest.mark.parametrize("prefix", ["sk_live_", "rk_live_", "rkcs_live_", "pk_test_", "pk_live_"])
def test_live_and_publishable_keys_are_rejected(prefix):
    with pytest.raises(ValueError, match="Only Stripe test secret keys"):
        Settings(stripe_secret_key=prefix + secrets.token_hex(24))


@pytest.mark.parametrize(
    "key", ["rkcs_test_", "sk_test_", "rk_test_", "rkcs_test_bad key", " rkcs_test_abc", "rkcs_test_abc\n"]
)
def test_incomplete_or_whitespace_keys_are_rejected(key):
    with pytest.raises(ValueError, match="Only Stripe test secret keys"):
        Settings(stripe_secret_key=key)
