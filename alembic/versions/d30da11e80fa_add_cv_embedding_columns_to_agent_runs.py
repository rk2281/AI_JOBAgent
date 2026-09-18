"""add cv_embedding columns to agent_runs

Revision ID: d30da11e80fa
Revises: 4ca67e97fc0e
Create Date: Day 14

Purely additive, three nullable columns, same shape as the job-side
embedding_status / jobs_embedded / embeddings_remaining_null columns
b3f7c21d9e40 already added -- see that migration for why almost
everything on this table is nullable. embed_cvs is a new graph node
(app/workflows/graph.py) with nowhere to report its own outcome in
agent_runs until now; tests/test_agent_runs.py's drift tests require
build_run_summary()'s keys and this table's columns to match exactly
apart from `id`, which is what forces this migration to exist rather
than the state.py/run_agent.py change standing alone.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d30da11e80fa"
down_revision = "4ca67e97fc0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("cv_embedding_status", sa.String(length=32), nullable=True)
    )
    op.add_column("agent_runs", sa.Column("cvs_embedded", sa.Integer(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("cv_embeddings_remaining_null", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "cv_embeddings_remaining_null")
    op.drop_column("agent_runs", "cvs_embedded")
    op.drop_column("agent_runs", "cv_embedding_status")
