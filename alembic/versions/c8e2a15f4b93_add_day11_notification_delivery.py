"""add day 11 notification delivery and feedback constraints

Revision ID: c8e2a15f4b93
Revises: b3f7c21d9e40
Create Date: Day 11

Written by hand, like every migration here. `alembic revision
--autogenerate` cannot see the two HNSW indexes created with raw
op.execute() in 563b5bb86690, and has twice proposed dropping them.

SAFE BECAUSE BOTH TABLES ARE EMPTY, VERIFIED RATHER THAN ASSUMED

    SELECT count(*) FROM notifications   -> 0
    SELECT count(*) FROM user_feedback   -> 0

That is what makes `trigger_source NOT NULL DEFAULT 'scheduled'` safe:
no existing row is handed a provenance it never had. It is the same
argument recommendations.weight_covered's docstring makes for its own
NOT NULL server default, and it is only an argument while the count is
zero -- a later migration adding a column to a populated table must
make it again, against that day's count.

The same zero is what makes the two new unique rules safe. A unique
constraint added to a table already holding a violating pair fails at
ALTER TABLE, loudly; both duplicate checks were run first and returned
0 groups, so neither can fail that way.

THE INDEX PREDICATE SAYS 'SENT', UPPERCASE, AND THAT IS NOT A TYPO

`notifications.status` is the PostgreSQL enum `notification_status`,
and SQLAlchemy's Enum() persists a Python enum by its NAME, not its
value. So the labels in the database are 'PENDING', 'SENT', 'FAILED',
while NotificationStatus.SENT.value is the lowercase "sent". Confirmed
against pg_enum before this file was written, not remembered.

This matters more than a spelling usually does. Written as
`WHERE status::text = 'sent'` the index would be created SUCCESSFULLY
and then match no row ever, because no row's text is ever lowercase --
duplicate prevention would be entirely absent while the migration
reported success and every ORM-level test stayed green. That is
CLAUDE.md section 0's failure exactly: a success status that is not
success. Written as `WHERE status = 'sent'` it fails loudly instead,
which is the better of the two wrong answers but still wrong.

Hence the test that guards this is not "does the index exist". It
inserts two SENT rows for one (user_id, job_id) against real Postgres
and requires an IntegrityError. An index that exists and does not fire
fails it.

WHY trigger_source IS A VARCHAR AND NOT A NEW ENUM TYPE

Day 2c showed what native enums cost: they survive downgrade() as
orphaned types and have to be dropped by hand, which is why
7255dfea3285's downgrade carries two hand-added sa.Enum(...).drop()
calls, and why users.onboarding_state is a VARCHAR with its values
enforced in Python. A third enum type here would buy nothing and
inherit both problems -- including the case-sensitivity trap above.

WHY notifications BECOMES AN ATTEMPT TABLE

uq_notification_user_job made a failed delivery permanent: one row per
(user_id, job_id) forever, so a Telegram outage during the only attempt
locked that user out of that job with no way back. The replacement
states the rule that was actually wanted -- at most one SUCCESSFUL
delivery per pair, any number of attempts -- and states it in the
database rather than in application code, so a second process racing
the first cannot produce two sent rows by interleaving two checks.

No arbitrary attempt ceiling is added. A transient outage must not
permanently cost a user a job; failures stay visible as rows instead.

WHY user_feedback GETS (user_id, job_id, action) AND NOT (user_id, job_id)

Because contradictory feedback is information. "Interested" then "Not
Relevant" is a person changing their mind and both rows are kept; the
same action twice is a double tap and the second is dropped. A
two-column constraint would silently discard the second, DIFFERENT
action while the user saw an acknowledgement for a record that does not
exist.

downgrade() reverses all four, and restores the original unique
constraint. It can only succeed if the table holds no pair that
violates the old rule -- which, after this migration has been used as
intended, it may well do. That is stated rather than defended against:
a downgrade that silently deleted attempt rows to fit the old shape
would be destroying evidence to make a schema fit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8e2a15f4b93"
down_revision = "b3f7c21d9e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- notifications becomes an attempt/history table --------------------
    op.drop_constraint("uq_notification_user_job", "notifications", type_="unique")

    op.add_column(
        "notifications",
        sa.Column(
            "trigger_source",
            sa.String(length=32),
            nullable=False,
            server_default="scheduled",
        ),
    )

    # Uppercase 'SENT'. See the module docstring -- this is the one line in
    # the file where a plausible-looking spelling silently disables the
    # whole constraint.
    op.create_index(
        "uq_notification_sent_user_job",
        "notifications",
        ["user_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'SENT'"),
    )

    # --- feedback keeps history, drops only exact repeats ------------------
    op.create_unique_constraint(
        "uq_user_feedback_user_job_action",
        "user_feedback",
        ["user_id", "job_id", "action"],
    )

    # --- the delivery stage's counters, on the graph's own run row ---------
    #
    # Every one is nullable, and that is the same rule the rest of this
    # table follows: a run whose notify branch never executed has NO
    # opinion about how many messages were sent, and 0 would state one.
    # Absent is not zero -- the same distinction as an abstained signal
    # column, which CLAUDE.md section 1 forbids defaulting to 0.0.
    op.add_column(
        "agent_runs",
        sa.Column("notification_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("notifications_eligible_selected", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("notifications_attempted", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("notifications_sent", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("notifications_failed", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("notifications_skipped_duplicate", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("notifications_users_deactivated", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "notifications_users_deactivated")
    op.drop_column("agent_runs", "notifications_skipped_duplicate")
    op.drop_column("agent_runs", "notifications_failed")
    op.drop_column("agent_runs", "notifications_sent")
    op.drop_column("agent_runs", "notifications_attempted")
    op.drop_column("agent_runs", "notifications_eligible_selected")
    op.drop_column("agent_runs", "notification_status")

    op.drop_constraint(
        "uq_user_feedback_user_job_action", "user_feedback", type_="unique"
    )

    op.drop_index("uq_notification_sent_user_job", table_name="notifications")
    op.drop_column("notifications", "trigger_source")

    # Fails if the table now holds two attempts for one pair. Deliberate:
    # see the module docstring. Deleting rows to fit the old shape would
    # be destroying the evidence this table exists to keep.
    op.create_unique_constraint(
        "uq_notification_user_job", "notifications", ["user_id", "job_id"]
    )
