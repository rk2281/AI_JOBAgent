"""add agent_runs

Revision ID: b3f7c21d9e40
Revises: 9a4e7c1d5b82
Create Date: Day 10

Written by hand, like every migration here. `alembic revision
--autogenerate` cannot see the two HNSW indexes created with raw
op.execute() in 563b5bb86690, and has twice proposed dropping them.

PURELY ADDITIVE. One new table, no column on any existing table is
added, altered or dropped. That is a deliberate boundary rather than a
coincidence: `agent_runs` records what the workflow graph DECIDED, and
every stage it drives already writes its own row to `ingestion_runs`,
`embedding_runs` or `scoring_runs`. Nothing here needs to reach into
those.

WHY THERE ARE NO FOREIGN KEYS

`ingestion_run_id` and `scoring_run_id` are plain integers, not
references. This table is an audit trail and must outlive the rows it
describes; a foreign key would make cleaning up an old `scoring_runs`
row fail against a record of the run that produced it, or silently
cascade it away. The id is stored so a person can go and look, not so
the database can insist the two still agree.

WHY ALMOST EVERYTHING IS NULLABLE

`started_at` is the only NOT NULL column besides the primary key,
because it is the only value known when the row is opened. The row is
written at the START of a run and completed at the end, so that a run
killed mid-flight leaves a row with `finished_at IS NULL` rather than
leaving nothing at all -- the same shape `scoring_runs` uses, and for
the reason its own docstring gives: a crash that erases its own
evidence is the hardest kind to investigate.

Beyond that, NULL carries meaning here and must not be defaulted away.
`jobs_enriched` is NULL after a run whose enrichment stage returned
before computing it; the `users_skipped_*` columns are NULL when scoring
never ran at all. Those are absences, not zeroes, and giving them a
server default of 0 would make an unfinished run indistinguishable from
a complete one that found nothing -- the same mistake as defaulting an
abstained signal column to 0.0, which CLAUDE.md section 1 forbids.

downgrade() drops the table. Nothing references it, so there is no
ordering constraint in either direction -- unlike 9a4e7c1d5b82, where
the recommendations columns had to go before the table they pointed at.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3f7c21d9e40"
down_revision = "9a4e7c1d5b82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        # Opened at start; the only thing known then.
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # The graph's decisions.
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
        sa.Column("notify_branch", sa.String(length=32), nullable=True),
        sa.Column("notify_eligible", sa.Integer(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=True),
        sa.Column("writes_prevented", sa.Boolean(), nullable=True),
        sa.Column("computation_performed", sa.Boolean(), nullable=True),
        sa.Column("persistence_performed", sa.Boolean(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        # JSONB rather than ARRAY(Text): four of these hold stage names and
        # are homogeneous strings, but `errors` has no writer yet and the
        # first one will be an exception path, which wants a dict.
        sa.Column("stages_attempted", postgresql.JSONB(), nullable=True),
        sa.Column("stages_skipped", postgresql.JSONB(), nullable=True),
        sa.Column("stages_computed", postgresql.JSONB(), nullable=True),
        sa.Column("stages_persisted", postgresql.JSONB(), nullable=True),
        sa.Column("errors", postgresql.JSONB(), nullable=True),
        # Targets.
        sa.Column("users_considered", sa.Integer(), nullable=True),
        sa.Column("users_with_profile", sa.Integer(), nullable=True),
        sa.Column("users_with_embedded_cv", sa.Integer(), nullable=True),
        # Per-stage outcomes. The *_run_id columns are NOT foreign keys.
        sa.Column("ingestion_status", sa.String(length=32), nullable=True),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("jobs_inserted", sa.Integer(), nullable=True),
        sa.Column("embedding_status", sa.String(length=32), nullable=True),
        sa.Column("jobs_embedded", sa.Integer(), nullable=True),
        sa.Column("embeddings_remaining_null", sa.Integer(), nullable=True),
        sa.Column("enrichment_status", sa.String(length=32), nullable=True),
        sa.Column("jobs_enriched", sa.Integer(), nullable=True),
        sa.Column("enrichment_remaining_null", sa.Integer(), nullable=True),
        sa.Column("scoring_status", sa.String(length=32), nullable=True),
        sa.Column("scoring_run_id", sa.Integer(), nullable=True),
        # The scoring funnel as the graph saw it. users_skipped_no_cv keeps
        # its imprecise name deliberately (CLAUDE.md section 1); the three
        # after it say which cause applied.
        sa.Column("users_skipped_no_cv", sa.Integer(), nullable=True),
        sa.Column("users_skipped_no_profile", sa.Integer(), nullable=True),
        sa.Column("users_skipped_no_active_cv", sa.Integer(), nullable=True),
        sa.Column("users_skipped_cv_not_embedded", sa.Integer(), nullable=True),
        sa.Column("users_scored", sa.Integer(), nullable=True),
        sa.Column("jobs_scored", sa.Integer(), nullable=True),
        sa.Column("pairs_scored", sa.Integer(), nullable=True),
        sa.Column("jobs_skipped_no_embedding", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="One row per workflow graph run. See app/db/models/agent.py.",
    )

    # An unfinished run is the row worth finding, and the only way to find
    # one is `finished_at IS NULL`. A partial index costs almost nothing
    # because the rows it covers are, by design, rare.
    op.create_index(
        "ix_agent_runs_unfinished",
        "agent_runs",
        ["started_at"],
        unique=False,
        postgresql_where=sa.text("finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_unfinished", table_name="agent_runs")
    op.drop_table("agent_runs")
