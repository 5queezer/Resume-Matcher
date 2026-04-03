"""RSA key management for RS256 JWT signing."""

from __future__ import annotations

import logging
from pathlib import Path

from joserfc.jwk import RSAKey

logger = logging.getLogger(__name__)

_cached_keys: tuple[RSAKey, RSAKey, str] | None = None


def compute_kid(public_key: RSAKey) -> str:
    """Compute JWK Thumbprint (RFC 7638) as key ID."""
    return public_key.thumbprint()


def load_rsa_keys(
    pem_data: str | None = None,
    key_file: str | None = None,
) -> None:
    """Load or generate RSA key pair. Call once at startup.

    Priority: pem_data > key_file > auto-generate (saves to key_file if given).
    """
    global _cached_keys  # noqa: PLW0603
    if _cached_keys is not None:
        return

    private_key: RSAKey
    if pem_data:
        logger.info("Loading RSA key from PEM data")
        try:
            private_key = RSAKey.import_key(pem_data)
        except Exception as e:
            raise ValueError(f"Failed to load RSA key from PEM data: {e}") from e
    elif key_file:
        path = Path(key_file)
        if path.exists():
            logger.info("Loading RSA key from file: %s", path)
            try:
                private_key = RSAKey.import_key(path.read_text())
            except Exception as e:
                raise ValueError(f"Failed to load RSA key from {path}: {e}") from e
        else:
            logger.info("Generating RSA key and saving to: %s", path)
            private_key = RSAKey.generate_key(2048)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(private_key.as_pem(private=True))
            path.chmod(0o600)
    else:
        logger.info("Auto-generating RSA key pair (in-memory only)")
        private_key = RSAKey.generate_key(2048)

    pub_dict = private_key.as_dict(private=False)
    public_key = RSAKey.import_key(pub_dict)
    kid = compute_kid(public_key)
    _cached_keys = (private_key, public_key, kid)
    logger.info("RSA keys loaded — kid=%s", kid)


def get_private_key() -> RSAKey:
    """Return cached RSA private key. Raises RuntimeError if not loaded."""
    if _cached_keys is None:
        raise RuntimeError("RSA keys not loaded — call load_rsa_keys() first")
    return _cached_keys[0]


def get_public_key() -> RSAKey:
    """Return cached RSA public key. Raises RuntimeError if not loaded."""
    if _cached_keys is None:
        raise RuntimeError("RSA keys not loaded — call load_rsa_keys() first")
    return _cached_keys[1]


def get_kid() -> str:
    """Return JWK Thumbprint (kid). Raises RuntimeError if not loaded."""
    if _cached_keys is None:
        raise RuntimeError("RSA keys not loaded — call load_rsa_keys() first")
    return _cached_keys[2]


def get_jwks() -> dict:
    """Return public key in JWKS format (RFC 7517). No private components."""
    pub = get_public_key()
    pub_dict = pub.as_dict(private=False)
    return {"keys": [{
        "kty": pub_dict["kty"],
        "use": "sig",
        "alg": "RS256",
        "kid": get_kid(),
        "n": pub_dict["n"],
        "e": pub_dict["e"],
    }]}


def reset_keys() -> None:
    """Clear cached keys (testing only)."""
    global _cached_keys  # noqa: PLW0603
    _cached_keys = None
