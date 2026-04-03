"""Tests for RSA key management module."""

from pathlib import Path

import pytest

from app.auth.keys import (
    compute_kid,
    get_jwks,
    get_kid,
    get_private_key,
    get_public_key,
    load_rsa_keys,
    reset_keys,
)


@pytest.fixture(autouse=True)
def _clean_keys():
    """Reset key cache before and after each test."""
    reset_keys()
    yield
    reset_keys()


class TestAccessorsBeforeLoad:
    def test_get_private_key_raises(self):
        with pytest.raises(RuntimeError, match="not loaded"):
            get_private_key()

    def test_get_public_key_raises(self):
        with pytest.raises(RuntimeError, match="not loaded"):
            get_public_key()

    def test_get_kid_raises(self):
        with pytest.raises(RuntimeError, match="not loaded"):
            get_kid()


class TestAutoGeneration:
    def test_generates_key_pair(self):
        load_rsa_keys()
        assert get_private_key() is not None
        assert get_public_key() is not None
        assert isinstance(get_kid(), str)
        assert len(get_kid()) > 0

    def test_key_is_2048_bit(self):
        import base64
        load_rsa_keys()
        d = get_private_key().as_dict(private=True)
        n_bytes = base64.urlsafe_b64decode(d["n"] + "==")
        assert len(n_bytes) == 256  # 2048 bits = 256 bytes


class TestCaching:
    def test_second_load_is_noop(self):
        load_rsa_keys()
        kid1 = get_kid()
        load_rsa_keys()  # should not regenerate
        assert get_kid() == kid1

    def test_reset_clears_cache(self):
        load_rsa_keys()
        reset_keys()
        with pytest.raises(RuntimeError, match="not loaded"):
            get_private_key()


class TestLoadFromPEM:
    def test_malformed_pem_raises_valueerror(self):
        with pytest.raises(ValueError, match="Failed to load RSA key from PEM data"):
            load_rsa_keys(pem_data="not-a-valid-pem")

    def test_load_from_pem_data(self):
        from joserfc.jwk import RSAKey
        key = RSAKey.generate_key(2048)
        pem = key.as_pem(private=True).decode("utf-8")
        load_rsa_keys(pem_data=pem)
        assert get_private_key().thumbprint() == key.thumbprint()


class TestLoadFromFile:
    def test_load_from_existing_file(self, tmp_path: Path):
        from joserfc.jwk import RSAKey
        key = RSAKey.generate_key(2048)
        pem_file = tmp_path / "test_key.pem"
        pem_file.write_text(key.as_pem(private=True).decode("utf-8"))
        load_rsa_keys(key_file=str(pem_file))
        assert get_private_key().thumbprint() == key.thumbprint()

    def test_auto_generate_saves_to_file(self, tmp_path: Path):
        pem_file = tmp_path / "auto_key.pem"
        assert not pem_file.exists()
        load_rsa_keys(key_file=str(pem_file))
        assert pem_file.exists()
        content = pem_file.read_text()
        assert "PRIVATE KEY" in content

    def test_generated_file_has_restricted_perms(self, tmp_path: Path):
        pem_file = tmp_path / "secure_key.pem"
        load_rsa_keys(key_file=str(pem_file))
        assert pem_file.stat().st_mode & 0o777 == 0o600


class TestPriority:
    def test_pem_data_over_file(self, tmp_path: Path):
        from joserfc.jwk import RSAKey
        key1 = RSAKey.generate_key(2048)
        key2 = RSAKey.generate_key(2048)
        pem_file = tmp_path / "key.pem"
        pem_file.write_text(key2.as_pem(private=True).decode("utf-8"))
        load_rsa_keys(
            pem_data=key1.as_pem(private=True).decode("utf-8"),
            key_file=str(pem_file),
        )
        assert get_private_key().thumbprint() == key1.thumbprint()


class TestJWKS:
    def test_jwks_format(self):
        load_rsa_keys()
        jwks = get_jwks()
        assert "keys" in jwks
        assert len(jwks["keys"]) == 1
        key = jwks["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["use"] == "sig"
        assert "kid" in key
        assert "n" in key
        assert "e" in key

    def test_jwks_excludes_private_components(self):
        load_rsa_keys()
        key = get_jwks()["keys"][0]
        for private_field in ("d", "p", "q", "dp", "dq", "qi"):
            assert private_field not in key


class TestKIDDeterminism:
    def test_same_key_same_kid(self):
        from joserfc.jwk import RSAKey
        key = RSAKey.generate_key(2048)
        pem = key.as_pem(private=True).decode("utf-8")
        load_rsa_keys(pem_data=pem)
        kid1 = get_kid()
        reset_keys()
        load_rsa_keys(pem_data=pem)
        kid2 = get_kid()
        assert kid1 == kid2
