"""All database access concerning an AgentRun row.

WHY TWO WRITES AND NOT ONE

`build_run_summary()` does not exist until the run has finished, so a
single INSERT at the end is the obvious implementation. It is also the
one `ScoringRunRepository.start()` was written to avoid, in as many
words: "a pass killed mid-flight leaves a row stuck at 'running' rather
than no row at all. A crash that erases its own evidence is the hardest
kind to investigate."

That reasoning does not stop applying because this table happens to be
populated from a dict built at the end. A workflow run is the longest
thing this project does -- ingestion, embedding, an enrichment pass that
can take 27 to 84 minutes, then scoring -- and it is about to be run by
a scheduler with nobody watching. The run most worth having a record of
is precisely the one that did not reach the end.

So: `start()` opens a row with `started_at` and nothing else, and
`finish()` fills in the whole summary. An interrupted run leaves a row
with **`finished_at IS NULL` and every counter NULL**, which is
findable (`ix_agent_runs_unfinished` indexes exactly that) and
unambiguous.

NO STATUS ENUM

"Unfinished" is `finished_at IS NULL`, not a status value. `scoring_runs`
draws the same distinction the same way. A status enum for interrupted
runs is Day 11's territory; naming the consequence is not the same as
encoding it, and encoding it now would mean choosing terms for states
nobody has observed yet.

WHERE THIS IS CALLED FROM, AND WHY NOT FROM A NODE

`scripts/run_agent.py`, around the graph invocation -- never from inside
`app/workflows/`. That is not a preference: CLAUDE.md section 1 records
that `app/workflows/` imports no repository, deliberately, "because
`resolve_targets` calls a service so the rule needs no exception, and an
exception is a hole to grow into". Persisting from a node would be that
exception. The driver owns the database; the graph owns the decisions.

NO CLOCK

Both methods take their timestamps as arguments. `start()` is handed the
same `started_at` the graph state carries, and `finish()` reads
`finished_at` out of the summary, where the `finalise` node stamped it
before `build_run_summary()` ever saw the state. Nothing here calls
now(), for the same reason the summary does not: a record that
timestamps itself cannot be reproduced from its own inputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentRun

# Filled in by finish(). Everything build_run_summary() returns except
# the two timestamps, which are parsed, and `status`, which is written
# alongside them. Derived from the summary's own keys at call time rather
# than listed here -- a hand-maintained list is the thing
# test_agent_run_columns_cover_every_summary_key exists to make
# unnecessary.
_TIMESTAMP_KEYS = ("started_at", "finished_at")


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    """ISO-8601 string -> aware datetime, or pass a datetime through.

    The summary hands over strings, because that is what survives a
    LangGraph checkpoint -- see app/workflows/state.py's rule that
    everything at a node boundary is JSON-serialisable. They are
    timezone-aware and fromisoformat() round-trips them exactly, so this
    is a parse rather than a coercion: nothing is invented and nothing is
    dropped.
    """
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class AgentRunRepository:
    """All database access concerning an AgentRun row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, started_at: str | datetime) -> AgentRun:
        """Open a run row before the graph is invoked.

        Deliberately writes almost nothing: at this point almost nothing
        is known. What matters is that the row exists, so that a process
        killed during a 40-minute enrichment pass leaves evidence rather
        than silence.
        """
        run = AgentRun(started_at=_parse_timestamp(started_at))
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish(self, run_id: int, summary: dict[str, Any]) -> None:
        """Write the whole summary onto an already-open row.

        Takes the summary dict verbatim and copies every key that has a
        column. Copying by key rather than by an explicit argument list
        is what makes the drift test meaningful: a key added to the
        summary lands here automatically, and the test is what guarantees
        it has somewhere to land.

        A key with no column would be silently dropped. That is exactly
        the failure the drift test exists to catch, so it is caught at
        test time rather than defended against here -- a runtime guard
        would turn a loud test failure into a quiet production one.
        """
        run = await self._session.get(AgentRun, run_id)
        if run is None:  # pragma: no cover - the caller just created it
            raise ValueError(f"no agent_runs row with id {run_id}")

        columns = {column.name for column in AgentRun.__table__.columns}
        for key, value in summary.items():
            if key not in columns:
                continue
            if key in _TIMESTAMP_KEYS:
                value = _parse_timestamp(value)
            setattr(run, key, value)

        await self._session.flush()

    async def latest(self) -> AgentRun | None:
        """The most recently started run. For reading a result back."""
        result = await self._session.execute(
            select(AgentRun).order_by(AgentRun.started_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def count(self) -> int:
        """How many rows exist. Used by the dry-run test, which asserts
        this does not move."""
        result = await self._session.execute(select(AgentRun.id))
        return len(result.all())
