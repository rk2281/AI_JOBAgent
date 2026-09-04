"""The agent_runs table, and the two things that can silently rot it.

No database. These are schema and wiring assertions, driven off the
model's actual columns and the summary's actual keys rather than off a
list somebody maintains by hand -- a hand-maintained list is the thing
that goes stale, and a stale list is indistinguishable from a correct
one right up until the day it matters.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.models.agent import AgentRun
from app.workflows.state import build_run_summary, initial_state


def _run(coro):
    return asyncio.run(coro)


def _summary_keys() -> set[str]:
    state = initial_state(started_at="2026-09-04T00:00:00+00:00")
    state["finished_at"] = "2026-09-04T00:01:00+00:00"
    return set(build_run_summary(state).keys())


def _columns() -> set[str]:
    return {column.name for column in AgentRun.__table__.columns}


# --- drift ---------------------------------------------------------------


def test_every_summary_key_has_a_column() -> None:
    """The direction that must fail, and the reason this table exists.

    Day 11 adds a notification node and the summary grows fields. A field
    added without a column is SILENTLY DROPPED on write -- the repository
    copies by key and skips what it cannot store -- while every other
    test in this repository stays green and the run reports success. The
    row would simply be missing a number nobody thought to look for.

    Driven off both actuals so it cannot pass by agreeing with a stale
    copy of either.
    """
    missing = _summary_keys() - _columns()
    assert not missing, f"summary keys with no agent_runs column: {sorted(missing)}"


def test_columns_beyond_the_summary_are_acknowledged_not_forbidden() -> None:
    """The other direction, asserted differently on purpose.

    A column with no summary key is not a bug. `id` is one and always
    will be, and Day 11 may add bookkeeping this table needs and the
    summary does not produce -- a scheduler id, or the status enum for
    interrupted runs that is explicitly out of scope today. Making that
    direction FAIL would force the summary to grow a key for every
    storage detail, which inverts the dependency: the summary is the
    source of truth and the table follows it.

    But dead columns accumulate quietly, so extras are enumerated rather
    than ignored -- the same `==` rather than `<=` shape that
    test_all_expected_tables_are_registered uses, for the same reason.
    """
    assert _columns() - _summary_keys() == {"id"}


def test_the_two_sets_are_the_same_size_apart_from_the_primary_key() -> None:
    assert len(_columns()) == len(_summary_keys()) + 1


# --- the rules the table inherits from the summary -----------------------


def test_absent_stays_nullable() -> None:
    """`jobs_enriched` is None after a dry run and the `users_skipped_*`
    columns are None when scoring never ran. A NOT NULL with a server
    default of 0 would make an unfinished run indistinguishable from a
    complete one that found nothing -- the same mistake as defaulting an
    abstained signal column to 0.0, which is a section 1 row."""
    for name in (
        "jobs_enriched",
        "enrichment_remaining_null",
        "users_skipped_no_cv",
        "users_skipped_no_profile",
        "users_skipped_no_active_cv",
        "users_skipped_cv_not_embedded",
        "finished_at",
        "status",
    ):
        assert AgentRun.__table__.c[name].nullable is True, name


def test_started_at_is_the_only_required_column_besides_the_key() -> None:
    """Everything else arrives at finish(), and a run that never finishes
    legitimately has none of it."""
    required = {c.name for c in AgentRun.__table__.columns if not c.nullable}
    assert required == {"id", "started_at"}


def test_timestamps_are_timezone_aware() -> None:
    """The summary hands over aware ISO-8601 strings, and `scoring_runs`
    stores DateTime(timezone=True). A naive column here would make the
    obvious first question -- how does this run line up with its scoring
    run -- need a cast."""
    for name in ("started_at", "finished_at"):
        assert AgentRun.__table__.c[name].type.timezone is True, name


def test_the_run_id_columns_are_not_foreign_keys() -> None:
    """This table is an audit trail and must outlive the rows it
    describes. A foreign key would make cleaning up an old scoring_runs
    row fail against the record of the run that produced it."""
    for name in ("ingestion_run_id", "scoring_run_id"):
        assert not AgentRun.__table__.c[name].foreign_keys, name


# --- a dry run writes no row ---------------------------------------------


def test_a_dry_run_never_opens_a_row(monkeypatch) -> None:
    """A row saying writes_prevented=true would itself be a write the run
    promised not to make, and the contradiction would sit inside the very
    record contradicting it.

    Asserted by making the repository explode if it is touched at all,
    rather than by counting rows afterwards -- the suite has no database,
    and a count that stayed the same would also be what a broken query
    returned.
    """
    import scripts.run_agent as run_agent

    class _Exploding:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a dry run must not touch agent_runs")

    monkeypatch.setattr(run_agent, "AgentRunRepository", _Exploding)

    class _FakeGraph:
        async def ainvoke(self, state):
            return dict(state, finished_at="2026-09-04T00:01:00+00:00")

    monkeypatch.setattr(run_agent, "build_graph", lambda: _FakeGraph())
    monkeypatch.setattr(run_agent, "_print_summary", lambda summary: None)

    class _Args:
        user_id = None
        dry_run = True
        skip_ingestion = skip_embedding = skip_enrichment = False
        keywords = locations = None
        max_pages = enrichment_limit = None

    exit_code = _run(run_agent.run(_Args()))
    assert exit_code == 0


def test_a_real_run_opens_and_finishes_exactly_one_row(monkeypatch) -> None:
    """The counterpart. Two calls, in order, on the same id -- open
    before the graph, finish after it. A single write at the end would
    leave no record of a run killed during a 40-minute enrichment pass.
    """
    import scripts.run_agent as run_agent

    calls: list[str] = []

    class _FakeRun:
        id = 42

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def start(self, started_at):
            calls.append(f"start:{started_at}")
            return _FakeRun()

        async def finish(self, run_id, summary):
            calls.append(f"finish:{run_id}:{summary['status']}")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(run_agent, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(run_agent, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(run_agent, "_print_summary", lambda summary: None)

    class _FakeGraph:
        async def ainvoke(self, state):
            return dict(
                state,
                finished_at="2026-09-04T00:01:00+00:00",
                stages_computed=["score_and_rank"],
                scoring={"status": "complete_no_qualifying", "pairs_scored": 294},
            )

    monkeypatch.setattr(run_agent, "build_graph", lambda: _FakeGraph())

    class _Args:
        user_id = None
        dry_run = False
        skip_ingestion = skip_embedding = skip_enrichment = False
        keywords = locations = None
        max_pages = enrichment_limit = None

    _run(run_agent.run(_Args()))

    assert len(calls) == 2
    assert calls[0].startswith("start:")
    assert calls[1].startswith("finish:42:")


def test_the_row_is_opened_before_the_graph_runs(monkeypatch) -> None:
    """Ordering is the whole point of the two-write shape. If the open
    happened after ainvoke, a crash inside the graph would leave nothing,
    which is the outcome ScoringRunRepository.start's docstring calls the
    hardest kind to investigate."""
    import scripts.run_agent as run_agent

    order: list[str] = []

    class _FakeRun:
        id = 7

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def start(self, started_at):
            order.append("start")
            return _FakeRun()

        async def finish(self, run_id, summary):
            order.append("finish")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    class _FakeGraph:
        async def ainvoke(self, state):
            order.append("graph")
            return dict(state, finished_at="2026-09-04T00:01:00+00:00")

    monkeypatch.setattr(run_agent, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(run_agent, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(run_agent, "build_graph", lambda: _FakeGraph())
    monkeypatch.setattr(run_agent, "_print_summary", lambda summary: None)

    class _Args:
        user_id = None
        dry_run = False
        skip_ingestion = skip_embedding = skip_enrichment = False
        keywords = locations = None
        max_pages = enrichment_limit = None

    _run(run_agent.run(_Args()))

    assert order == ["start", "graph", "finish"]


def test_a_crash_inside_the_graph_leaves_the_opened_row(monkeypatch) -> None:
    """What an interrupted run leaves behind, asserted rather than
    asserted-in-prose: start() has already happened, finish() has not, so
    the row exists with finished_at NULL. Named, not encoded as a status
    enum -- that is Day 11's territory."""
    import scripts.run_agent as run_agent

    order: list[str] = []

    class _FakeRun:
        id = 9

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def start(self, started_at):
            order.append("start")
            return _FakeRun()

        async def finish(self, run_id, summary):  # pragma: no cover
            order.append("finish")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    class _ExplodingGraph:
        async def ainvoke(self, state):
            raise RuntimeError("killed mid-run")

    monkeypatch.setattr(run_agent, "AgentRunRepository", _FakeRepo)
    monkeypatch.setattr(run_agent, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(run_agent, "build_graph", lambda: _ExplodingGraph())

    class _Args:
        user_id = None
        dry_run = False
        skip_ingestion = skip_embedding = skip_enrichment = False
        keywords = locations = None
        max_pages = enrichment_limit = None

    with pytest.raises(RuntimeError):
        _run(run_agent.run(_Args()))

    assert order == ["start"]
