"""Password hashing with argon2id."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against an argon2id hash."""
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False
