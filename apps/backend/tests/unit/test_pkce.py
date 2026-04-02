"""Tests for PKCE S256 code challenge verification."""

import base64
import hashlib

import pytest
from app.auth.pkce import verify_code_challenge


class TestPKCE:
    def _make_challenge(self, verifier: str) -> str:
        """Helper: compute S256 challenge from verifier."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def test_valid_s256_challenge(self) -> None:
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = self._make_challenge(verifier)
        assert verify_code_challenge(verifier, challenge, "S256") is True

    def test_wrong_verifier(self) -> None:
        verifier = "correct-verifier"
        challenge = self._make_challenge(verifier)
        assert verify_code_challenge("wrong-verifier", challenge, "S256") is False

    def test_plain_method_rejected(self) -> None:
        with pytest.raises(ValueError, match="S256"):
            verify_code_challenge("verifier", "challenge", "plain")

    def test_empty_verifier_rejected(self) -> None:
        assert verify_code_challenge("", "somechallenge", "S256") is False

    def test_rfc7636_test_vector(self) -> None:
        # RFC 7636 Appendix B test vector
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected_challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assert verify_code_challenge(verifier, expected_challenge, "S256") is True
