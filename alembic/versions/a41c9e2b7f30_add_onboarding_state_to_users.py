"""add onboarding_state to users

Revision ID: a41c9e2b7f30
Revises: 563b5bb86690
Create Date: 2026-08-28 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a41c9e2b7f30'
down_revision: Union[str, Sequence[str], None] = '563b5bb86690'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is what makes this safe on a table that already has
    # rows. The column is NOT NULL, so without a default the ALTER would
    # fail outright on any existing user. Every pre-existing user is
    # placed at "new", which is correct: they have not been through the
    # flow that this column tracks.
    op.add_column(
        'users',
        sa.Column(
            'onboarding_state',
            sa.String(length=32),
            server_default='new',
            nullable=False,
        ),
    )

    # A partial index over the unfinished states. The queries that will
    # want this — "who dropped out of onboarding" on Day 11 — never ask
    # for completed users, and excluding them keeps the index small as
    # the table grows.
    op.execute(
        "CREATE INDEX ix_users_onboarding_state_pending "
        "ON users (onboarding_state) "
        "WHERE onboarding_state <> 'complete'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_users_onboarding_state_pending")
    op.drop_column('users', 'onboarding_state')
