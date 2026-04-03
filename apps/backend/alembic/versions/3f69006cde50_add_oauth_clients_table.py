"""add oauth_clients table

Revision ID: 3f69006cde50
Revises: a1b2c3d4e5f6
Create Date: 2026-04-03 16:48:16.433014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f69006cde50'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('oauth_clients',
    sa.Column('client_id', sa.String(length=255), nullable=False),
    sa.Column('client_name', sa.String(length=255), nullable=True),
    sa.Column('redirect_uris', sa.JSON(), nullable=False),
    sa.Column('grant_types', sa.JSON(), nullable=False),
    sa.Column('response_types', sa.JSON(), nullable=False),
    sa.Column('token_endpoint_auth_method', sa.String(length=50), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('client_id')
    )
    # Seed the first-party client
    op.execute(
        sa.text(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, grant_types, response_types, token_endpoint_auth_method, is_active) "
            "VALUES (:cid, :name, :uris, :grants, :types, :method, 1)"
        ).bindparams(
            cid="resume-matcher-web",
            name="Resume Matcher Web",
            uris='["http://localhost:3000/callback", "http://127.0.0.1:3000/callback"]',
            grants='["authorization_code", "refresh_token"]',
            types='["code"]',
            method="none",
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('oauth_clients')
