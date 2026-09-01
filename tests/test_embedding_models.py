"""Tests for the Day 7 embedding schema.

No database. These read SQLAlchemy's metadata registry, the same way
tests/test_models.py does.

What is worth asserting here is narrow. The columns exist -- that much
a migration would reveal on the first run. What a migration would NOT
reveal is the two silent mismatches these guard against:

  - the vector() dimension drifting away from settings.embedding_
    dimension, which would fail only at INSERT time, after an entire
    embedding pass had been paid for

  - jobs and cv_versions acquiring different bookkeeping, which would
    fail at no time at all -- the CV side would simply lose the ability
    to distinguish "not attempted" from "failed", quietly, on one table
    only
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.db.models import Base, EmbeddingStatus

BOOKKEEPING_COLUMNS = (
    "embedding_model",
    "embedded_at",
    "embedding_attempts",
    "embedding_error",
    "embedding_source_hash",
)

EMBEDDED_TABLES = ("jobs", "cv_versions")


@pytest.mark.parametrize("table_name", EMBEDDED_TABLES)
@pytest.mark.parametrize("column_name", BOOKKEEPING_COLUMNS)
def test_both_embedded_tables_have_every_bookkeeping_column(
    table_name: str,
    column_name: str,
) -> None:
    assert column_name in Base.metadata.tables[table_name].c


@pytest.mark.parametrize("table_name", EMBEDDED_TABLES)
def test_vector_dimension_matches_the_configured_dimension(table_name: str) -> None:
    """The mismatch that would only surface at INSERT time.

    Postgres rejects a wrong-length vector rather than truncating it,
    which is the right behaviour but an expensive place to find out:
    by then the API calls have been made and the quota spent.
    """
    column = Base.metadata.tables[table_name].c.embedding
    assert column.type.dim == settings.embedding_dimension


@pytest.mark.parametrize("table_name", EMBEDDED_TABLES)
def test_attempts_is_not_nullable_and_defaults_to_zero(table_name: str) -> None:
    """0 vs NULL is the whole point of this column.

    A NULL attempts count could not be compared with > 0, so "never
    attempted" and "attempted and failed" would collapse back into one
    another, which is what these columns exist to prevent.
    """
    column = Base.metadata.tables[table_name].c.embedding_attempts
    assert column.nullable is False
    assert column.server_default is not None


@pytest.mark.parametrize("table_name", EMBEDDED_TABLES)
def test_embedding_itself_stays_nullable(table_name: str) -> None:
    """Day 6 made this nullable on purpose so ingestion never blocks on
    an API call. Day 7 must not quietly reverse that."""
    assert Base.metadata.tables[table_name].c.embedding.nullable is True


def test_embedding_runs_records_a_funnel_not_a_summary() -> None:
    columns = Base.metadata.tables["embedding_runs"].c

    for counter in (
        "candidates_considered",
        "skipped_empty_text",
        "attempted",
        "succeeded",
        "failed",
        "api_calls",
        "remaining_null",
    ):
        assert counter in columns


def test_embedding_runs_records_which_model_it_used() -> None:
    """Two models on this key both return 768 dimensions. Without this
    column a mixed table cannot be traced back to the run that mixed
    it."""
    assert "model" in Base.metadata.tables["embedding_runs"].c


def test_status_separates_the_kinds_of_nothing() -> None:
    """NOTHING_TO_DO and NO_SOURCE_ROWS both mean zero rows embedded
    and mean opposite things: work already done, versus nothing to work
    on. A single status would merge them."""
    values = {member.value for member in EmbeddingStatus}

    assert values == {
        "running",
        "complete",
        "partial",
        "nothing_to_do",
        "no_source_rows",
        "all_failed",
        "provider_error",
        "quota_exceeded",
    }


def test_status_is_a_string_enum_so_it_stores_as_varchar() -> None:
    assert EmbeddingStatus.COMPLETE == "complete"


def test_configured_model_is_the_one_that_was_measured() -> None:
    """gemini-embedding-2 was tested on the live API and rejected: it
    ignores task_type (cosine 1.000000 between DOCUMENT and QUERY) and
    returned one vector for a batch of eight."""
    assert settings.gemini_embedding_model == "gemini-embedding-001"


def test_batch_size_is_within_what_was_verified() -> None:
    """A batch is all-or-nothing, and only 8 has been confirmed to
    return one vector per input in the correct order."""
    assert 1 <= settings.embedding_batch_size <= 8
