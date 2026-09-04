"""The Day 11 schema, asserted against Base.metadata rather than a database.

These are shape assertions: what the model declares. Whether PostgreSQL
actually ENFORCES it is a different question, and a more important one
-- a partial unique index can be declared correctly, created
successfully and match zero rows forever. That question is answered in
tests/test_notification_constraints.py against a real database, because
it is the one thing no amount of metadata inspection can settle.

Both files exist because either alone would be misleading.
"""

from __future__ import annotations

from app.db.models import Base
from app.db.models.recommendation import (
    NOTIFICATION_TRIGGER_SOURCES,
    TRIGGER_SOURCE_MANUAL_TEST,
    TRIGGER_SOURCE_SCHEDULED,
    Notification,
    NotificationStatus,
)


def _table(name: str):
    return Base.metadata.tables[name]


# --- notifications became an attempt table -------------------------------


def test_the_old_one_row_per_pair_constraint_is_gone() -> None:
    """It made a FAILED delivery permanent: one row per (user_id,
    job_id) forever, so a Telegram outage during the only attempt locked
    that user out of that job with no way back."""
    constraints = {c.name for c in _table("notifications").constraints}

    assert "uq_notification_user_job" not in constraints


def test_at_most_one_successful_delivery_per_pair_is_declared() -> None:
    indexes = {index.name: index for index in _table("notifications").indexes}

    assert "uq_notification_sent_user_job" in indexes
    index = indexes["uq_notification_sent_user_job"]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["user_id", "job_id"]


def test_the_partial_index_predicate_matches_the_enum_labels_postgres_holds() -> None:
    """THE test in this file, and the one a plausible mistake defeats.

    SQLAlchemy's Enum() persists a Python enum by its NAME, so the
    labels in PostgreSQL are 'PENDING', 'SENT', 'FAILED' -- uppercase --
    while NotificationStatus.SENT.value is the lowercase "sent".

    A predicate written `status::text = 'sent'` would be created
    SUCCESSFULLY by the migration and then match no row ever. Duplicate
    prevention would be entirely absent while the migration reported
    success and every ORM-level test stayed green: a success status that
    is not success.

    Asserted against the enum's own name rather than a hard-coded
    'SENT', so renaming the member cannot leave this passing against a
    stale literal.
    """
    index = {i.name: i for i in _table("notifications").indexes}[
        "uq_notification_sent_user_job"
    ]
    predicate = str(index.dialect_options["postgresql"]["where"])

    assert NotificationStatus.SENT.name in predicate, predicate
    assert NotificationStatus.SENT.value not in predicate, (
        "the lowercase VALUE would match nothing in PostgreSQL"
    )


def test_the_enum_is_persisted_by_name_which_is_why_the_predicate_is_uppercase() -> None:
    """Pinning the premise the test above depends on.

    If a future SQLAlchemy upgrade or a values_callable made the enum
    persist by VALUE, the predicate would silently stop matching and
    duplicate prevention would vanish with no failure anywhere. This
    fails first, and points at the reason.
    """
    column = _table("notifications").c.status

    assert column.type.enum_class is NotificationStatus
    assert column.type.values_callable is None, (
        "a values_callable would persist by value and break the index predicate"
    )


# --- trigger_source ------------------------------------------------------


def test_trigger_source_is_not_null_with_a_scheduled_default() -> None:
    """The production value is the default, so a writer that forgets is
    correct rather than unrecorded."""
    column = _table("notifications").c.trigger_source

    assert column.nullable is False
    assert column.server_default is not None
    assert TRIGGER_SOURCE_SCHEDULED in str(column.server_default.arg)


def test_trigger_source_is_a_varchar_and_not_a_fourth_enum_type() -> None:
    """Day 2c showed what native enums cost: they survive downgrade() as
    orphaned types needing a hand-written drop, which is why
    7255dfea3285's downgrade carries two sa.Enum(...).drop() calls and
    why users.onboarding_state is a VARCHAR.

    It also sidesteps the uppercase/lowercase trap above entirely.
    """
    column = _table("notifications").c.trigger_source

    assert column.type.python_type is str
    assert not hasattr(column.type, "enum_class")


def test_the_two_trigger_sources_are_distinct_and_enumerated() -> None:
    assert TRIGGER_SOURCE_SCHEDULED != TRIGGER_SOURCE_MANUAL_TEST
    assert NOTIFICATION_TRIGGER_SOURCES == {"scheduled", "manual_test"}


# --- feedback ------------------------------------------------------------


def test_feedback_is_unique_on_three_columns_including_the_action() -> None:
    """Three, not two. The third is what lets a person change their
    mind: (user, job) alone would silently drop a second, DIFFERENT
    action while the handler still acknowledged it."""
    constraints = {
        c.name: c
        for c in _table("user_feedback").constraints
        if c.name == "uq_user_feedback_user_job_action"
    }

    assert constraints, "the feedback uniqueness rule is missing"
    columns = [c.name for c in constraints["uq_user_feedback_user_job_action"].columns]
    assert sorted(columns) == ["action", "job_id", "user_id"]


def test_no_two_column_uniqueness_rule_exists_on_feedback() -> None:
    """The mistake this schema is defined against, asserted directly so
    that adding it later is a failure rather than a silent behaviour
    change."""
    for constraint in _table("user_feedback").constraints:
        columns = {c.name for c in constraint.columns}
        assert columns != {"user_id", "job_id"}, (
            "a (user_id, job_id) rule would discard contradictory feedback"
        )


# --- what must not have changed ------------------------------------------


def test_the_recommendation_signal_columns_are_still_nullable() -> None:
    """NULL is abstain. Defaulting these to 0.0 destroys the abstain
    model, and CLAUDE.md section 1 names this as a do-not-fix row.

    Restated here because Day 11 is the first work to touch this module
    since the rule was written, and the cheapest way for it to be broken
    is by somebody tidying the file it lives in.
    """
    recommendations = _table("recommendations")

    for name in (
        "semantic_score",
        "skill_score",
        "experience_score",
        "location_score",
        "title_score",
        "semantic_raw",
    ):
        assert recommendations.c[name].nullable is True, name


def test_the_notification_columns_on_agent_runs_are_all_nullable() -> None:
    """Absent is not zero. A run whose notify branch never executed has
    no opinion about how many messages were sent."""
    agent_runs = _table("agent_runs")

    for name in (
        "notification_status",
        "notifications_eligible_selected",
        "notifications_attempted",
        "notifications_sent",
        "notifications_failed",
        "notifications_skipped_duplicate",
        "notifications_users_deactivated",
    ):
        assert agent_runs.c[name].nullable is True, name


def test_notifications_still_cascades_when_a_user_is_deleted() -> None:
    """Unchanged by Day 11, and worth asserting because the table's
    constraints were edited."""
    foreign_keys = {
        fk.parent.name: fk for fk in _table("notifications").foreign_keys
    }

    assert foreign_keys["user_id"].ondelete == "CASCADE"
    assert foreign_keys["job_id"].ondelete == "CASCADE"
    # SET NULL, not CASCADE: deleting an old recommendation must not
    # erase the record that it was delivered.
    assert foreign_keys["recommendation_id"].ondelete == "SET NULL"


def test_the_notification_model_is_still_registered() -> None:
    assert Notification.__tablename__ == "notifications"
    assert "notifications" in Base.metadata.tables
