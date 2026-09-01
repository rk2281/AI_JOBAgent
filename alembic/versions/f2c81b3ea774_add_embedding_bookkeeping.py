"""add embedding bookkeeping columns and embedding_runs

Revision ID: f2c81b3ea774
Revises: d7a3f1c92b40
Create Date: Day 7

Written by hand rather than generated, for the same reason every
migration in this project is: `alembic revision --autogenerate` does
not see the two HNSW indexes created with raw op.execute() in
563b5bb86690, because HNSW is not expressible through the ORM's
Index() and so they are absent from Base.metadata. Autogenerate has
twice proposed dropping them. Run `python -m scripts.check_indexes`
after upgrade AND after downgrade regardless -- this migration does
not touch either index, and that claim is worth verifying rather than
trusting.

Adds nothing that changes existing behaviour. The five new columns on
`jobs` and `cv_versions` are all nullable except embedding_attempts,
which is NOT NULL with server_default '0' so that the 99 existing job
rows and every existing cv_versions row acquire a correct value
without a data migration.

No index is created on the new columns. At 99 rows a partial index on
`embedding IS NULL` would never be chosen by the planner, and an index
nobody can demonstrate the use of is one more thing for a
down-up-down-up cycle to drift on. Revisit when the table is large.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2c81b3ea774'
down_revision: Union[str, Sequence[str], None] = 'd7a3f1c92b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_COLUMN_TABLES = ('jobs', 'cv_versions')


def upgrade() -> None:
    for table in EMBEDDING_COLUMN_TABLES:
        op.add_column(table, sa.Column('embedding_model', sa.String(length=128), nullable=True))
        op.add_column(table, sa.Column('embedded_at', sa.DateTime(timezone=True), nullable=True))
        op.add_column(
            table,
            sa.Column(
                'embedding_attempts',
                sa.Integer(),
                server_default='0',
                nullable=False,
            ),
        )
        op.add_column(table, sa.Column('embedding_error', sa.Text(), nullable=True))
        op.add_column(table, sa.Column('embedding_source_hash', sa.String(length=64), nullable=True))

    op.create_table(
        'embedding_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(length=32), nullable=False),
        sa.Column(
            'status',
            sa.String(length=32),
            server_default='running',
            nullable=False,
        ),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('candidates_considered', sa.Integer(), nullable=False),
        sa.Column('skipped_empty_text', sa.Integer(), nullable=False),
        sa.Column('attempted', sa.Integer(), nullable=False),
        sa.Column('succeeded', sa.Integer(), nullable=False),
        sa.Column('failed', sa.Integer(), nullable=False),
        sa.Column('api_calls', sa.Integer(), nullable=False),
        sa.Column('remaining_null', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_embedding_runs_scope', 'embedding_runs', ['scope'])
    op.create_index('ix_embedding_runs_status', 'embedding_runs', ['status'])


def downgrade() -> None:
    op.drop_index('ix_embedding_runs_status', table_name='embedding_runs')
    op.drop_index('ix_embedding_runs_scope', table_name='embedding_runs')
    op.drop_table('embedding_runs')

    # Reverse order of upgrade(), so a down-up-down-up cycle lands on
    # the same schema every time rather than drifting.
    for table in reversed(EMBEDDING_COLUMN_TABLES):
        op.drop_column(table, 'embedding_source_hash')
        op.drop_column(table, 'embedding_error')
        op.drop_column(table, 'embedding_attempts')
        op.drop_column(table, 'embedded_at')
        op.drop_column(table, 'embedding_model')
