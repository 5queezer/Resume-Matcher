"""Tests for argon2id password hashing."""

import pytest
from app.auth.password import hash_password, verify_password


class TestPasswordHashing:
    def test_hash_returns_argon2id_string(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert hashed.startswith("$argon2id$")

    def test_hash_is_not_plaintext(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert hashed != "mysecretpassword"

    def test_verify_correct_password(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert verify_password("mysecretpassword", hashed) is True

    def test_verify_wrong_password(self) -> None:
        hashed = hash_password("mysecretpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_different_passwords_produce_different_hashes(self) -> None:
        h1 = hash_password("password1")
        h2 = hash_password("password2")
        assert h1 != h2

    def test_same_password_produces_different_hashes(self) -> None:
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2  # salt should differ
