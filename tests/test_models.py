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
    "recommendations",
    "notifications",
    "user_feedback",
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


def test_user_dependents_cascade_on_delete() -> None:
    for table_name in ("profiles", "cvs", "recommendations", "user_feedback"):
        foreign_keys = Base.metadata.tables[table_name].c.user_id.foreign_keys
        assert all(fk.ondelete == "CASCADE" for fk in foreign_keys)
