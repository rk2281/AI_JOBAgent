"""Complain when the newest agent_runs row is too old. Read-only.

    python -m scripts.check_run_freshness
    python -m scripts.check_run_freshness --max-age-hours 26

WHY THIS EXISTS

CLAUDE.md, "Open after Day 10 Part 3":

    Nothing observes whether the nightly run happened. A skipped night
    writes no log and leaves no agent_runs row, and nothing looks for
    either absence.

That is section 0's shape exactly. Every counter this project has is
written BY a run, so a run that never started produces no bad number --
it produces no number, and no check anywhere fires on a number that
was never written. Task Scheduler survives reboots and exposes
LastTaskResult, which is more survivable than APScheduler and still
observed by nothing.

This is one query against the one table the run itself writes, so it is
independent of the scheduling mechanism: it would notice Task Scheduler
being disabled, the laptop being off, the venv path going stale, the
task running as a user who cannot read the repository, and somebody
deleting the task. All of those look identical from here, which is
correct -- the question is "did the work happen", and the answer is no
in every one of them.

WHY 26 HOURS

The run is nightly. A 24-hour window would fire on a run that started
at 02:05 instead of 02:00, so the threshold has to exceed the period by
more than the jitter. Two hours of slack is enough for a delayed start
and a long enrichment pass without being so wide that a fully missed
night hides inside it.

THREE OUTCOMES, NOT TWO

An empty table is not the same as a stale one. A brand new deployment
that has never run and a deployment whose scheduler died last week both
have no recent row, and only the second is an incident. They are given
different exit codes so a monitor can treat them differently, and
different words so a human is not sent looking for a scheduler that was
never registered.

EXIT CODES

    0  fresh    -- a run started inside the window
    1  stale    -- rows exist, the newest is outside the window
    2  empty    -- no agent_runs rows at all
    3  error    -- could not reach the database to find out

3 is separate from 1 and 2 because "I could not tell" is not evidence
of anything, and a monitor that reads it as "stale" pages somebody
about the wrong system.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

if sys.platform == "win32":
    # psycopg's async driver cannot use the ProactorEventLoop that
    # Windows defaults to. Set before anything imports the database
    # layer.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import func, select  # noqa: E402

from app.db.models.agent import AgentRun  # noqa: E402
from app.db.session import (  # noqa: E402
    dispose_engine,
    init_engine,
    session_scope,
)

DEFAULT_MAX_AGE_HOURS = 26.0

FRESH = "fresh"
STALE = "stale"
EMPTY = "empty"

EXIT_CODES = {FRESH: 0, STALE: 1, EMPTY: 2}
EXIT_ERROR = 3


@dataclass(frozen=True)
class Verdict:
    """What the newest row says, and how old it is."""

    state: str
    age_hours: float | None
    newest_started_at: datetime | None
    newest_finished_at: datetime | None
    total_runs: int
    unfinished_runs: int

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.state]


def assess(
    *,
    newest_started_at: datetime | None,
    newest_finished_at: datetime | None,
    total_runs: int,
    unfinished_runs: int,
    now: datetime,
    max_age_hours: float,
) -> Verdict:
    """Decide fresh / stale / empty. Pure, so it is testable at the boundary.

    Separated from the query for the same reason `is_notify_eligible`
    was extracted from `run_scoring`: a threshold comparison that can
    only be exercised by arranging a database is a threshold nobody
    tests at its exact boundary.

    The comparison is `<=`, so a run at exactly the limit is FRESH. A
    threshold written with `<` misses the boundary (CLAUDE.md section
    2), and here the boundary is the ordinary case -- a nightly run is
    always almost exactly one period old.
    """
    if newest_started_at is None:
        return Verdict(
            state=EMPTY,
            age_hours=None,
            newest_started_at=None,
            newest_finished_at=None,
            total_runs=total_runs,
            unfinished_runs=unfinished_runs,
        )

    age_hours = (now - newest_started_at).total_seconds() / 3600.0

    return Verdict(
        state=FRESH if age_hours <= max_age_hours else STALE,
        age_hours=age_hours,
        newest_started_at=newest_started_at,
        newest_finished_at=newest_finished_at,
        total_runs=total_runs,
        unfinished_runs=unfinished_runs,
    )


async def read_state(now: datetime, max_age_hours: float) -> Verdict:
    """One trip to the database. Reads, writes nothing.

    Three values in one session so they describe one moment: the newest
    row, the total count, and how many rows never finished. The last is
    not part of the freshness verdict -- a run that started is evidence
    the scheduler fired, which is what this asks -- but it is printed,
    because "it ran every night and died every night" and "it ran fine"
    are both FRESH and should not read the same.
    """
    async with session_scope() as session:
        newest = (
            await session.execute(
                select(AgentRun).order_by(AgentRun.started_at.desc()).limit(1)
            )
        ).scalar_one_or_none()

        total = int(
            (await session.execute(select(func.count(AgentRun.id)))).scalar_one()
        )

        unfinished = int(
            (
                await session.execute(
                    select(func.count(AgentRun.id)).where(
                        AgentRun.finished_at.is_(None)
                    )
                )
            ).scalar_one()
        )

    return assess(
        newest_started_at=newest.started_at if newest else None,
        newest_finished_at=newest.finished_at if newest else None,
        total_runs=total,
        unfinished_runs=unfinished,
        now=now,
        max_age_hours=max_age_hours,
    )


def report(verdict: Verdict, max_age_hours: float) -> None:
    print(f"agent_runs rows    {verdict.total_runs}")
    print(f"unfinished rows    {verdict.unfinished_runs}")
    print(f"window             {max_age_hours:g} hours")

    if verdict.state == EMPTY:
        print("newest run         (none)")
        print("result             EMPTY")
        print("")
        print("No run has ever been recorded. On a new deployment this is")
        print("expected. On an established one it means agent_runs was")
        print("truncated, or the scheduled task has never once fired --")
        print("scripts/schedule_agent.ps1 -SelfTest proves the command line,")
        print("not that Windows starts it.")
        return

    print(f"newest started     {verdict.newest_started_at.isoformat()}")
    finished = (
        verdict.newest_finished_at.isoformat()
        if verdict.newest_finished_at
        else "(never finished)"
    )
    print(f"newest finished    {finished}")
    print(f"age                {verdict.age_hours:.1f} hours")

    if verdict.state == FRESH:
        print("result             FRESH")
        if verdict.newest_finished_at is None:
            print("")
            print("The newest run started inside the window but never")
            print("finished. That is what a process killed mid-flight looks")
            print("like, and it is why the row is opened before the graph")
            print("runs. Freshness says the scheduler fired; it does not say")
            print("the run completed.")
        return

    print("result             STALE")
    print("")
    print("The scheduler has not started a run inside the window. This one")
    print("check cannot say which cause applied -- task disabled, machine")
    print("off, interpreter path stale, task running as a user who cannot")
    print("read the repository, task deleted. All of them mean the same")
    print("thing: last night's recommendations were not produced.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.check_run_freshness",
        description="Complain when no agent run has started recently. Read-only.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=(
            "How old the newest run may be before it counts as stale. "
            f"Default {DEFAULT_MAX_AGE_HOURS:g}, which is a nightly period "
            "plus slack for a delayed start."
        ),
    )
    arguments = parser.parse_args(argv)

    async def run() -> Verdict:
        init_engine()
        try:
            return await read_state(
                datetime.now(timezone.utc), arguments.max_age_hours
            )
        finally:
            await dispose_engine()

    try:
        verdict = asyncio.run(run())
    except Exception as error:  # noqa: BLE001 -- any failure is "cannot tell"
        # The exception TYPE and message, not the connection string: a
        # DATABASE_URL carries a password, and every leak in this
        # project came from something handling a secret incidentally
        # rather than from printing one on purpose.
        print(
            f"cannot check       {type(error).__name__}",
            file=sys.stderr,
        )
        return EXIT_ERROR

    report(verdict, arguments.max_age_hours)
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
