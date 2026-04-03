"""Unit tests for Google OAuth state packing/unpacking."""

import time

import pytest

from app.auth.google import pack_state, unpack_state


SECRET = "test-secret-key-for-hmac"


class TestStatePacking:
    def test_roundtrip(self) -> None:
        data = {
            "state": "abc-123",
            "code_challenge": "challenge",
            "redirect_uri": "http://localhost:3000/callback",
            "nonce": "nonce-value",
            "ts": int(time.time()),
        }
        packed = pack_state(data, SECRET)
        result = unpack_state(packed, SECRET)
        assert result["state"] == "abc-123"
        assert result["code_challenge"] == "challenge"
        assert result["nonce"] == "nonce-value"

    def test_tampered_payload_rejected(self) -> None:
        data = {"state": "original", "ts": int(time.time())}
        packed = pack_state(data, SECRET)
        payload, sig = packed.rsplit(".", 1)
        tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B")
        with pytest.raises(ValueError, match="Invalid state signature"):
            unpack_state(f"{tampered}.{sig}", SECRET)

    def test_tampered_signature_rejected(self) -> None:
        data = {"state": "original", "ts": int(time.time())}
        packed = pack_state(data, SECRET)
        with pytest.raises(ValueError, match="Invalid state signature"):
            unpack_state(packed + "x", SECRET)

    def test_expired_state_rejected(self) -> None:
        data = {"state": "old", "ts": int(time.time()) - 700}
        packed = pack_state(data, SECRET)
        with pytest.raises(ValueError, match="State expired"):
            unpack_state(packed, SECRET, max_age=600)

    def test_wrong_secret_rejected(self) -> None:
        data = {"state": "test", "ts": int(time.time())}
        packed = pack_state(data, SECRET)
        with pytest.raises(ValueError, match="Invalid state signature"):
            unpack_state(packed, "wrong-secret")

    def test_malformed_state_rejected(self) -> None:
        with pytest.raises(ValueError, match="Malformed state"):
            unpack_state("no-dot-separator", SECRET)

    def test_fresh_state_accepted(self) -> None:
        data = {"state": "fresh", "ts": int(time.time()) - 300}
        packed = pack_state(data, SECRET)
        result = unpack_state(packed, SECRET, max_age=600)
        assert result["state"] == "fresh"
