from __future__ import annotations

import base64
import json

import pytest

from platform_protocol_contracts import (
    IdentityAssertionError,
    mint_identity_assertion,
    verify_identity_assertion,
)


KEY = b"identity-assertion-test-key-32-bytes-minimum"
VALUES = {
    "issuer": "enterprise-edge",
    "audience": "platform-v6",
    "key_id": "local-v1",
    "subject": "alice@example.test",
    "display_name": "Alice Operator",
    "roles": ("researcher", "admin"),
    "project": "groundwater-demo",
    "now": 1_800_000_000,
    "ttl_seconds": 60,
    "jti": "assertion-fixture-0001",
}


def _token(**changes):
    return mint_identity_assertion(KEY, **{**VALUES, **changes})


def _verify(token: str, **changes):
    expected = {
        key: VALUES[key]
        for key in ("issuer", "audience", "key_id", "project", "now")
    }
    return verify_identity_assertion(token, KEY, **{**expected, **changes})


def test_round_trip_returns_only_verified_claims():
    identity = _verify(_token())

    assert identity.subject == "alice@example.test"
    assert identity.display_name == "Alice Operator"
    assert identity.roles == ("admin", "researcher")
    assert identity.project == "groundwater-demo"
    assert identity.expires_at == VALUES["now"] + 60
    assert identity.jti == "assertion-fixture-0001"


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("issuer", "other-edge"),
        ("audience", "other-product"),
        ("key_id", "other-key"),
        ("project", "other-project"),
    ],
)
def test_context_mismatch_is_rejected(change, value):
    with pytest.raises(IdentityAssertionError):
        _verify(_token(), **{change: value})


def test_expired_and_future_assertions_are_rejected():
    with pytest.raises(IdentityAssertionError, match="EXPIRED"):
        _verify(_token(ttl_seconds=30), now=VALUES["now"] + 61)
    with pytest.raises(IdentityAssertionError, match="EXPIRED"):
        _verify(_token(now=VALUES["now"] + 31), now=VALUES["now"])


def test_signature_tampering_is_rejected_before_claims_are_trusted():
    token = _token()
    header, payload, signature = token.split(".")
    body = json.loads(base64.urlsafe_b64decode(payload + "=="))
    body["roles"] = ["admin"]
    tampered = base64.urlsafe_b64encode(
        json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()

    with pytest.raises(IdentityAssertionError):
        _verify(f"{header}.{tampered}.{signature}")


def test_short_keys_and_unbounded_ttls_are_rejected():
    with pytest.raises(ValueError, match="KEY_INVALID"):
        mint_identity_assertion(b"short", **VALUES)
    with pytest.raises(ValueError, match="TTL_INVALID"):
        _token(ttl_seconds=301)
