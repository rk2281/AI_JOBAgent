"""Tests for the ORM model definitions.

These run without a database — they inspect SQLAlchemy's metadata
registry rather than connecting to PostgreSQL.
"""

from app.db.models import Base

EXPECTED_TABLES = {
    "users",
    "user_preferences",
    "profiles",
    "cvs",
    "cv_versions",
    "skills",
    "jobs",
    "job_skills",
    "ingestion_runs",
    "ingestion_rejects",
    "embedding_runs",
    "scoring_runs",
    "recommendations",
    "notifications",
    "user_feedback",
    # Day 10: one row per workflow graph run. Added here deliberately --
    # this assertion is `==` rather than `<=` precisely so a new table
    # cannot appear without somebody acknowledging it.
    "agent_runs",
}


def test_all_expected_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_telegram_id_is_unique_and_big_enough() -> None:
    column = Base.metadata.tables["users"].c.telegram_id

    assert column.unique is True
    assert "BIGINT" in str(column.type).upper()


def test_jobs_are_deduplicated_by_source_and_external_id() -> None:
    constraints = Base.metadata.tables["jobs"].constraints
    names = {c.name for c in constraints}

    assert "uq_job_source_external" in names


def test_recommendation_stores_individual_signal_scores() -> None:
    columns = Base.metadata.tables["recommendations"].c

    for signal in (
        "semantic_score",
        "skill_score",
        "experience_score",
        "location_score",
        "title_score",
        "final_score",
    ):
        assert signal in columns


def test_recommendation_signal_scores_are_nullable() -> None:
    """A missing signal must be storable as NULL, not as 0.0.

    The whole abstain rule rests on this. When a job has no
    extractable skills, the skill signal scores NULL and its 30% is
    removed from the denominator; scoring it 0.0 instead would rank
    every non-tech job below every tech job for a reason that is a
    data gap, not a fit gap.

    NULL means "could not look". 0.0 means "looked, no match". If
    these columns are ever made NOT NULL, that distinction collapses
    into a single number and nothing anywhere would report it -- the
    scores would still compute, still rank, and still be wrong. This
    test is the only thing standing between those two states.
    """
    columns = Base.metadata.tables["recommendations"].c

    for signal in (
        "semantic_score",
        "skill_score",
        "experience_score",
        "location_score",
        "title_score",
    ):
        assert columns[signal].nullable, f"{signal} must stay nullable"

    # final_score is deliberately NOT in that list. A pair either has
    # a total or was not scored at all; there is no "abstained
    # overall".
    assert not columns["final_score"].nullable


def test_user_dependents_cascade_on_delete() -> None:
    for table_name in ("profiles", "cvs", "recommendations", "user_feedback"):
        foreign_keys = Base.metadata.tables[table_name].c.user_id.foreign_keys
        assert all(fk.ondelete == "CASCADE" for fk in foreign_keys)
