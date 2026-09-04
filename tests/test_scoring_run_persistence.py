"""What run_scoring writes to scoring_runs, checked against the schema.

No database. That is the entire point: the CompileError this guards
against reached a commit precisely BECAUSE the suite has no database and
every live check before it had been --dry-run, which returns before the
write. A test that needed Postgres would not have caught it either.

The failure being guarded is not the ordinary one. ScoringRunRepository
.finish() compiles the counters dict straight into
update(ScoringRun).values(**counters), and SQLAlchemy does not skip a key
it cannot place -- it raises `CompileError: Unconsumed column names`. So
the drift here is LOUD at runtime and completely invisible until a real
non-dry run happens, which in this project can be days apart.

Contrast with tests/test_agent_runs.py, which guards the opposite
failure: there a summary key with no column is SILENTLY DROPPED. Two
tables, two drift directions, two tests. Neither substitutes for the
other.
"""

from __future__ import annotations

from app.db.models.scoring import ScoringRun
from app.services.job_scoring import _Counters, build_scoring_run_counters


def _persisted_keys() -> set[str]:
    """The real dict, from the real function, with placeholder values.

    Built by calling the function rather than by reciting a list, so it
    cannot pass by agreeing with a stale copy of itself. The values are
    irrelevant; only the keys are under test.
    """
    return set(
        build_scoring_run_counters(
            _Counters(),
            semantic_raw_min=None,
            semantic_raw_max=None,
            semantic_raw_median=None,
            score_min=None,
            score_max=None,
            score_median=None,
            distinct_score_count=0,
        )
    )


def _columns() -> set[str]:
    return {column.name for column in ScoringRun.__table__.columns}


# --- the direction that must fail ----------------------------------------


def test_every_persisted_scoring_key_has_a_column() -> None:
    """The test that would have caught the Day 10 Part 3 crash.

    Adding a counter to the persisted dict without a column on
    scoring_runs raises CompileError on the first non-dry run and on no
    test, no dry run and no review. That is exactly what
    users_skipped_no_profile / _no_active_cv / _cv_not_embedded did:
    added in Part 1, green through 541 tests, green through a live dry
    run, and broken on the one path that writes.

    Driven off both actuals -- the function's real keys and the model's
    real columns -- so neither side can drift into agreement with a
    hand-maintained list.
    """
    missing = _persisted_keys() - _columns()
    assert not missing, (
        "persisted counter keys with no scoring_runs column: "
        f"{sorted(missing)} -- these raise CompileError: Unconsumed "
        "column names on the first non-dry run"
    )


# --- the other direction, asserted differently on purpose ----------------


def test_columns_beyond_the_persisted_counters_are_acknowledged() -> None:
    """A column with no counter key is not a bug.

    `id`, `started_at` and the rest are written by start() or by
    finish()'s own named arguments, not through the counters dict. Making
    this direction fail would force every storage detail to become a
    counter, which inverts the dependency: the columns are the schema and
    the dict is what one caller happens to write.

    So extras are ENUMERATED rather than forbidden -- the same `==`
    rather than `<=` shape as test_agent_runs.py and
    test_all_expected_tables_are_registered, and for the same reason: a
    new column cannot appear here without somebody acknowledging it in
    writing.
    """
    assert _columns() - _persisted_keys() == {
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "status",
        "weights_version",
        "error_message",
    }


def test_the_skip_breakdown_is_absent_from_the_persisted_dict() -> None:
    """Deliberate, and the reason is recorded where the dict is built.

    The three counters exist on the returned dict and in agent_runs.
    They are kept OUT of this one because scoring_runs has no columns for
    them. If somebody adds the columns, this test is the thing that
    should be updated in the same commit -- its failure is the reminder
    that the migration alone changes nothing, because nothing re-adds
    the keys to the dict.
    """
    persisted = _persisted_keys()
    for name in (
        "users_skipped_no_profile",
        "users_skipped_no_active_cv",
        "users_skipped_cv_not_embedded",
    ):
        assert name not in persisted, name
        assert name not in _columns(), name


def test_users_skipped_no_cv_is_persisted_and_the_breakdown_is_not() -> None:
    """The pair that makes the asymmetry legible rather than accidental.

    users_skipped_no_cv HAS a column and IS written; its three-way
    breakdown has neither. A reader looking at scoring_runs alone sees
    the total with no cause, which is the open item the Day 10 Part 3
    record names.
    """
    assert "users_skipped_no_cv" in _persisted_keys()
    assert "users_skipped_no_cv" in _columns()
