"""add job ingestion tables and job last_seen_at

Revision ID: d7a3f1c92b40
Revises: 4b481a8ea241
Create Date: Day 6

Written by hand rather than generated. `alembic revision --autogenerate`
does not see the two HNSW indexes -- they were created with raw
op.execute() in 563b5bb86690 because HNSW is not expressible through
the ORM's Index(), so they are absent from Base.metadata and
autogenerate has twice proposed dropping them. A hand-written
migration cannot make that mistake. Run `python -m scripts.check_indexes`
after upgrade AND after downgrade regardless.

The unique constraint on jobs.content_hash is the one change here with
a behavioural consequence rather than a structural one: it is what
makes a repost update an existing row instead of inserting a second.
content_hash stays nullable, and PostgreSQL treats NULLs as distinct
under a unique constraint, so rows predating this migration (which
have no hash) do not collide with each other.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd7a3f1c92b40'
down_revision: Union[str, Sequence[str], None] = '4b481a8ea241'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_jobs_last_seen_at',
        'jobs',
        ['last_seen_at'],
        unique=False,
    )

    # The index created alongside the column in 7255dfea3285 is
    # non-unique. Replacing it with a unique constraint is what turns
    # content_hash from a stored value into an enforced identity.
    op.drop_index('ix_jobs_content_hash', table_name='jobs')
    op.create_unique_constraint('uq_job_content_hash', 'jobs', ['content_hash'])

    op.create_table(
        'ingestion_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column(
            'status',
            sa.String(length=32),
            server_default='running',
            nullable=False,
        ),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('queries_attempted', sa.Integer(), nullable=False),
        sa.Column('pages_fetched', sa.Integer(), nullable=False),
        sa.Column('records_fetched', sa.Integer(), nullable=False),
        sa.Column('normalize_failed', sa.Integer(), nullable=False),
        sa.Column('validation_failed', sa.Integer(), nullable=False),
        sa.Column('filtered_out', sa.Integer(), nullable=False),
        sa.Column('duplicates', sa.Integer(), nullable=False),
        sa.Column('inserted', sa.Integer(), nullable=False),
        sa.Column('retired', sa.Integer(), nullable=False),
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
    op.create_index('ix_ingestion_runs_source', 'ingestion_runs', ['source'])
    op.create_index('ix_ingestion_runs_status', 'ingestion_runs', ['status'])

    op.create_table(
        'ingestion_rejects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('stage', sa.String(length=32), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column(
            'raw_payload',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['run_id'], ['ingestion_runs.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ingestion_rejects_run_id', 'ingestion_rejects', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_ingestion_rejects_run_id', table_name='ingestion_rejects')
    op.drop_table('ingestion_rejects')

    op.drop_index('ix_ingestion_runs_status', table_name='ingestion_runs')
    op.drop_index('ix_ingestion_runs_source', table_name='ingestion_runs')
    op.drop_table('ingestion_runs')

    # Put the non-unique index back exactly as 7255dfea3285 left it,
    # so that down-up-down-up cycles land on the same schema each time
    # rather than drifting.
    op.drop_constraint('uq_job_content_hash', 'jobs', type_='unique')
    op.create_index('ix_jobs_content_hash', 'jobs', ['content_hash'], unique=False)

    op.drop_index('ix_jobs_last_seen_at', table_name='jobs')
    op.drop_column('jobs', 'last_seen_at')
