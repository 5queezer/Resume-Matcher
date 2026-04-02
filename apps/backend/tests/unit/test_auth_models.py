"""Tests for auth-related ORM models."""

import pytest
from app.models import AuthorizationCode, Base, RefreshToken


class TestAuthModels:
    def test_authorization_code_table_name(self) -> None:
        assert AuthorizationCode.__tablename__ == "authorization_codes"

    def test_refresh_token_table_name(self) -> None:
        assert RefreshToken.__tablename__ == "refresh_tokens"

    def test_auth_tables_in_metadata(self) -> None:
        table_names = set(Base.metadata.tables.keys())
        assert "authorization_codes" in table_names
        assert "refresh_tokens" in table_names

    def test_authorization_code_has_user_fk(self) -> None:
        fks = {
            fk.target_fullname
            for col in AuthorizationCode.__table__.columns
            for fk in col.foreign_keys
        }
        assert "users.id" in fks

    def test_refresh_token_has_user_fk(self) -> None:
        fks = {
            fk.target_fullname
            for col in RefreshToken.__table__.columns
            for fk in col.foreign_keys
        }
        assert "users.id" in fks
