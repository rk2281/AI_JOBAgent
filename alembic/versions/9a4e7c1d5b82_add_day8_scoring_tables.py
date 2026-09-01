"""add Day 8 scoring tables and columns

Revision ID: 9a4e7c1d5b82
Revises: f2c81b3ea774
Create Date: Day 8

Written by hand, like every migration here. `alembic revision
--autogenerate` cannot see the two HNSW indexes created with raw
op.execute() in 563b5bb86690 -- HNSW is not expressible through the
ORM's Index(), so those indexes are absent from Base.metadata, and
autogenerate has twice proposed dropping them.

This migration does not touch either index. That claim is worth
verifying rather than trusting: run `python -m scripts.check_indexes`
after upgrade AND after downgrade. `scripts/show_schema.py` cannot
answer this -- it reads Base.metadata, where those indexes do not
appear.

ORDER MATTERS IN BOTH DIRECTIONS.

upgrade() creates scoring_runs BEFORE adding
recommendations.scoring_run_id, because that column carries a foreign
key to it and Postgres will not create a reference to a table that
does not exist yet.

downgrade() reverses exactly: the recommendations columns go first,
then the table. Dropping scoring_runs while a foreign key still
points at it fails, and a downgrade that fails halfway leaves a
schema that is neither version.

Nothing here changes existing behaviour.

  jobs           -- 8 new columns, all nullable except
                    skills_extraction_attempts and is_excluded, which
                    are NOT NULL with server defaults so the 99
                    existing rows acquire correct values without a
                    data migration.

  recommendations -- 6 new columns. weight_covered,
                    quality_multiplier and weights_version are NOT
                    NULL with server defaults. That is safe here only
                    because the table was empty when this was written
                    (SELECT count(*) returned 0), so no existing row
                    is handed a coverage or a multiplier it never
                    had. If this migration is ever applied to a
                    database where recommendations is populated, those
                    defaults become a lie that nothing reports.

  scoring_runs    -- new table.

No index is created on any of the new jobs columns. At 99 rows the
planner would not choose one, and an index nobody can demonstrate the
use of is one more thing for a down-up-down-up cycle to drift on.
Revisit when the table is large.

The foreign key on recommendations.scoring_run_id is deliberately
left unnamed, matching every other foreign key in this schema.
Postgres drops a column's constraints with the column, so downgrade
needs only drop_index and drop_column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '9a4e7c1d5b82'
down_revision: Union[str, Sequence[str], None] = 'f2c81b3ea774'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. scoring_runs, first, because recommendations references it ---
    op.create_table(
        'scoring_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.String(length=32),
            server_default='running',
            nullable=False,
        ),
        sa.Column(
            'weights_version',
            sa.Integer(),
            server_default='1',
            nullable=False,
        ),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),

        sa.Column('users_considered', sa.Integer(), nullable=False),
        sa.Column('users_skipped_no_cv', sa.Integer(), nullable=False),
        sa.Column('users_scored', sa.Integer(), nullable=False),

        sa.Column('jobs_considered', sa.Integer(), nullable=False),
        sa.Column('jobs_skipped_no_embedding', sa.Integer(), nullable=False),
        sa.Column('jobs_excluded_manual', sa.Integer(), nullable=False),
        sa.Column('jobs_scored', sa.Integer(), nullable=False),

        sa.Column('pairs_scored', sa.Integer(), nullable=False),

        sa.Column('abstain_semantic', sa.Integer(), nullable=False),
        sa.Column('abstain_skill', sa.Integer(), nullable=False),
        sa.Column('abstain_experience', sa.Integer(), nullable=False),
        sa.Column('abstain_location', sa.Integer(), nullable=False),
        sa.Column('abstain_title', sa.Integer(), nullable=False),

        sa.Column('semantic_clamped_high', sa.Integer(), nullable=False),
        sa.Column('semantic_clamped_low', sa.Integer(), nullable=False),
        sa.Column('semantic_raw_min', sa.Float(), nullable=True),
        sa.Column('semantic_raw_max', sa.Float(), nullable=True),
        sa.Column('semantic_raw_median', sa.Float(), nullable=True),

        sa.Column('quality_penalty_agency', sa.Integer(), nullable=False),
        sa.Column('quality_penalty_no_city', sa.Integer(), nullable=False),

        sa.Column('jobs_remote', sa.Integer(), nullable=False),
        sa.Column('jobs_hybrid', sa.Integer(), nullable=False),

        sa.Column('score_min', sa.Float(), nullable=True),
        sa.Column('score_max', sa.Float(), nullable=True),
        sa.Column('score_median', sa.Float(), nullable=True),
        sa.Column('distinct_score_count', sa.Integer(), nullable=False),

        sa.Column('notify_eligible', sa.Integer(), nullable=False),
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
    op.create_index('ix_scoring_runs_status', 'scoring_runs', ['status'])

    # The counter columns are NOT NULL with no server default, which
    # matches embedding_runs and ingestion_runs exactly. The Python
    # default=0 on the model supplies the value on every ORM insert,
    # and the run row is always created through the repository. A raw
    # SQL insert omitting them would fail loudly, which is the right
    # outcome: a run row with unknown counters is worse than no row.

    # --- 2. jobs: skill-extraction bookkeeping ---------------------------
    op.add_column(
        'jobs',
        sa.Column('skills_extracted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'jobs',
        sa.Column('skills_extraction_model', sa.String(length=128), nullable=True),
    )
    op.add_column(
        'jobs',
        sa.Column(
            'skills_extraction_attempts',
            sa.Integer(),
            server_default='0',
            nullable=False,
        ),
    )
    op.add_column('jobs', sa.Column('skills_extraction_error', sa.Text(), nullable=True))
    op.add_column(
        'jobs',
        sa.Column('skills_source_hash', sa.String(length=64), nullable=True),
    )
    op.add_column('jobs', sa.Column('work_mode', sa.String(length=16), nullable=True))
    op.add_column(
        'jobs',
        sa.Column(
            'is_excluded',
            sa.Boolean(),
            server_default='false',
            nullable=False,
        ),
    )
    op.add_column('jobs', sa.Column('exclusion_reason', sa.Text(), nullable=True))

    # --- 3. recommendations ----------------------------------------------
    op.add_column('recommendations', sa.Column('semantic_raw', sa.Float(), nullable=True))
    op.add_column(
        'recommendations',
        sa.Column('weight_covered', sa.Float(), server_default='0', nullable=False),
    )
    op.add_column(
        'recommendations',
        sa.Column(
            'quality_multiplier',
            sa.Float(),
            server_default='1.0',
            nullable=False,
        ),
    )
    op.add_column(
        'recommendations',
        sa.Column('weights_version', sa.Integer(), server_default='1', nullable=False),
    )
    op.add_column(
        'recommendations',
        sa.Column('inputs_fingerprint', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'recommendations',
        sa.Column(
            'scoring_run_id',
            sa.Integer(),
            sa.ForeignKey('scoring_runs.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_recommendations_scoring_run_id',
        'recommendations',
        ['scoring_run_id'],
    )


def downgrade() -> None:
    # Exact reverse of upgrade(), so a down-up-down-up cycle lands on
    # the same schema every time rather than drifting.

    op.drop_index('ix_recommendations_scoring_run_id', table_name='recommendations')
    op.drop_column('recommendations', 'scoring_run_id')
    op.drop_column('recommendations', 'inputs_fingerprint')
    op.drop_column('recommendations', 'weights_version')
    op.drop_column('recommendations', 'quality_multiplier')
    op.drop_column('recommendations', 'weight_covered')
    op.drop_column('recommendations', 'semantic_raw')

    op.drop_column('jobs', 'exclusion_reason')
    op.drop_column('jobs', 'is_excluded')
    op.drop_column('jobs', 'work_mode')
    op.drop_column('jobs', 'skills_source_hash')
    op.drop_column('jobs', 'skills_extraction_error')
    op.drop_column('jobs', 'skills_extraction_attempts')
    op.drop_column('jobs', 'skills_extraction_model')
    op.drop_column('jobs', 'skills_extracted_at')

    # Last, and only once nothing references it.
    op.drop_index('ix_scoring_runs_status', table_name='scoring_runs')
    op.drop_table('scoring_runs')
