"""Day 12 integration test 4 -- the LangGraph workflow, really executed.

    build_graph()                                   (REAL langgraph)
        -> resolve_targets -> score_and_rank
        -> decide_notification -> notify | finalise (REAL routing)
        -> build_run_summary -> agent_runs row      (REAL PostgreSQL)

`tests/test_workflow_*.py` already cover the graph's shape, its routing
in both directions and its state reducers, all without a database. What
they cannot cover is the thing that has bitten this project twice: a
summary key with nowhere to land, and a counters dict compiled into
`values(**counters)` against a table missing three columns. Both are
database facts, and both passed every test in the suite.

Ingestion, embedding and enrichment are skipped rather than faked. They
are the three stages that make network calls on someone else's
credentials, and `initial_state` already has flags for skipping them --
so this exercises the graph's real skip path rather than inventing a
test-only one.

THE NOTIFY BRANCH

`notify_eligible` is 0 on the project's live data, so the notify branch
has never executed outside a test. Here the fixture is built to clear
all three gates -- including the coverage floor, which needs the skill
and experience signals to have values -- so the branch is entered for
real and its counters land in a real `agent_runs` row.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models.agent import AgentRun
from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.job import Job, JobSkill
from app.db.models.profile import Profile
from app.db.models.recommendation import Notification, NotificationStatus
from app.db.models.skill import Skill
from app.db.models.user import User, UserPreference
from app.db.repositories.agent import AgentRunRepository
from app.db.session import session_scope
from app.workflows.graph import build_graph
from app.workflows.state import build_run_summary, initial_state

DIMENSIONS = 768
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _vector(degrees: float) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[0] = math.cos(math.radians(degrees))
    vector[1] = math.sin(math.radians(degrees))
    return vector


async def _seed_everything(*, with_skills: bool) -> dict[str, int]:
    """A candidate and one job that can clear every gate.

    `with_skills` controls whether the job has extracted skills and
    experience bounds. That single flag moves `weight_covered` from
    0.50 to 1.00, which is the difference between the live data's
    permanent `notify_eligible = 0` and a run that notifies.
    """
    async with session_scope() as session:
        user = User(telegram_id=920001, full_name="Priya Sharma")
        session.add(user)
        await session.flush()

        cv = CV(
            user_id=user.id,
            file_name="cv.docx",
            file_type="docx",
            storage_path="/dev/null",
            extraction_status=ExtractionStatus.COMPLETE.value,
        )
        session.add(cv)
        await session.flush()

        version = CVVersion(
            cv_id=cv.id,
            version=1,
            extracted_profile={"skills": ["Python"]},
            extraction_model="fixture",
            embedding=_vector(0),
            embedding_model="fixture",
            embedded_at=NOW,
        )
        session.add(version)
        await session.flush()

        session.add(
            Profile(
                user_id=user.id,
                summary="AI/ML engineer.",
                total_experience_years=1.0,
                current_title="ML Engineer",
                location="Delhi",
                skills=["python", "machine learning", "nlp", "fastapi", "sql"],
                experience=[],
                education=[],
                active_cv_version_id=version.id,
            )
        )
        session.add(
            UserPreference(
                user_id=user.id,
                target_roles=["Machine Learning Engineer"],
                preferred_locations=["Delhi"],
            )
        )

        job = Job(
            source="fixture",
            external_id="job-1",
            title="Machine Learning Engineer",
            company="Northwind Analytics",
            location="Delhi",
            description="Python, machine learning and NLP.",
            url="https://example.test/1",
            posted_at=NOW - timedelta(days=1),
            last_seen_at=NOW,
            content_hash="hash-1",
            embedding=_vector(2),
            embedding_model="fixture",
            embedded_at=NOW,
        )
        if with_skills:
            job.min_experience_years = 0
            job.max_experience_years = 3
        session.add(job)
        await session.flush()

        if with_skills:
            for name in ("Python", "Machine Learning", "NLP"):
                skill = Skill(name=name, normalized_name=name.lower())
                session.add(skill)
                await session.flush()
                # Added to the session by foreign key rather than
                # appended to `job.job_skills`: appending triggers a
                # lazy load of the collection, and a lazy load under an
                # async session raises MissingGreenlet. Observed here.
                session.add(
                    JobSkill(
                        job_id=job.id,
                        skill_id=skill.id,
                        is_required=True,
                    )
                )

        return {"user_id": user.id, "job_id": job.id}


class RecordingNotifier:
    """A TelegramNotifier-shaped object. No token, no network.

    Day 12 section 12 is explicit that Telegram must not be called
    verified on the strength of a test like this one. What this proves
    is everything up to the socket: gate, selection, attempt row,
    status transition, duplicate suppression. The socket itself is
    recorded as NOT VERIFIED in docs/TEST_RESULTS.md.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def __aenter__(self) -> "RecordingNotifier":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def send(self, *, chat_id: int, reply) -> Any:
        from app.integrations.telegram import SendResult

        self.sent.append((chat_id, reply.text))
        return SendResult(ok=True)


def _run_graph(**overrides: Any):
    """Drive the compiled graph the way scripts/run_agent.py does."""
    state = initial_state(
        skip_ingestion=True,
        skip_embedding=True,
        skip_enrichment=True,
        started_at=NOW.isoformat(),
        **overrides,
    )
    return build_graph().ainvoke(state)


def test_the_graph_runs_end_to_end_and_persists_an_agent_run(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The full driver shape: open a row, run the graph, complete it.

    The two writes are the point. A row is opened BEFORE the graph and
    completed after, so a run killed mid-flight leaves evidence rather
    than nothing -- and `ix_agent_runs_unfinished` indexes exactly that
    predicate. Asserting the row exists and is finished proves both
    halves ran.
    """

    async def body() -> dict[str, Any]:
        ids = await _seed_everything(with_skills=True)

        async with session_scope() as session:
            run_id = (await AgentRunRepository(session).start(NOW)).id

        final_state = await _run_graph(user_id=ids["user_id"], dry_run=True)
        summary = build_run_summary(final_state)

        async with session_scope() as session:
            await AgentRunRepository(session).finish(run_id, summary)

        async with session_scope() as session:
            row = (await session.execute(select(AgentRun))).scalar_one()
            return {
                "summary": summary,
                "row_status": row.status,
                "row_started": row.started_at,
                "row_finished": row.finished_at,
                "row_users_scored": row.users_scored,
                "row_pairs": row.pairs_scored,
                "row_notify_branch": row.notify_branch,
                "row_notify_eligible": row.notify_eligible,
                "stages_attempted": row.stages_attempted,
            }

    observed = run_with_database(body)

    assert observed["row_started"] is not None
    assert observed["row_finished"] is not None
    assert observed["row_users_scored"] == 1
    assert observed["row_pairs"] == 1

    # The skipped stages are recorded, not silently absent.
    assert "resolve_targets" in observed["stages_attempted"]
    assert "score_and_rank" in observed["stages_attempted"]
    assert "finalise" in observed["stages_attempted"]


def test_every_summary_key_lands_in_a_column(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The failure mode that survived two parts and a commit.

    `AgentRunRepository.complete()` compiles the summary into an
    UPDATE. A key with no column raises `CompileError: Unconsumed
    column names` -- but only against a real database, which is why
    `scoring_runs` shipped broken. This is that check, executed.
    """

    async def body() -> dict[str, Any]:
        ids = await _seed_everything(with_skills=True)

        async with session_scope() as session:
            run_id = (await AgentRunRepository(session).start(NOW)).id

        # dry_run=True, and NOT because a rehearsal is what we want to
        # test. The `notify` node calls run_notification_delivery(),
        # which builds a real TelegramNotifier with no injection seam,
        # so a non-dry graph invocation ALWAYS opens a socket to
        # api.telegram.org. That is recorded as a limitation in
        # docs/MVP_LIMITATIONS.md rather than worked around here: the
        # summary's key set is identical either way, so this test loses
        # nothing, and inventing a test-only injection path through the
        # graph would change production wiring to suit a test.
        final_state = await _run_graph(user_id=ids["user_id"], dry_run=True)
        summary = build_run_summary(final_state)

        # The write itself is the assertion: an unmapped key raises.
        async with session_scope() as session:
            await AgentRunRepository(session).finish(run_id, summary)

        return {"summary_keys": sorted(summary)}

    observed = run_with_database(body)
    assert observed["summary_keys"]


def test_the_notify_branch_delivers_and_records_an_attempt(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The branch that live data has never entered.

    With skills and experience bounds present, `weight_covered` reaches
    1.0 and all three gates clear, so `route_notification` returns
    "notify" for real. The delivery loop then runs against a real
    database with a notifier that records instead of connecting.
    """
    notifier = RecordingNotifier()

    async def body() -> dict[str, Any]:
        ids = await _seed_everything(with_skills=True)

        from app.services.job_scoring import run_scoring
        from app.services.notification_delivery import (
            deliver_notifications,
            select_notifiable,
        )

        scoring = await run_scoring(user_id=ids["user_id"])
        candidates = await select_notifiable(user_id=ids["user_id"])
        first = await deliver_notifications(candidates, notifier=notifier)

        # Second pass: the same recommendation must not be re-sent.
        repeat_candidates = await select_notifiable(user_id=ids["user_id"])
        second = await deliver_notifications(repeat_candidates, notifier=notifier)

        async with session_scope() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
            return {
                "scoring": scoring,
                "first": first,
                "second": second,
                "repeat_candidate_count": len(repeat_candidates),
                "notification_rows": [
                    (row.status, row.job_id, row.sent_at is not None) for row in rows
                ],
            }

    observed = run_with_database(body)

    assert observed["scoring"]["notify_eligible"] == 1
    assert observed["first"]["attempted"] == 1
    assert observed["first"]["sent"] == 1
    assert len(notifier.sent) == 1

    chat_id, text = notifier.sent[0]
    assert chat_id == 920001
    assert "Machine Learning Engineer" in text

    # One SENT row, and the second pass selects nothing at all: a job
    # already delivered leaves the candidate list before the loop.
    assert observed["notification_rows"] == [(NotificationStatus.SENT, 1, True)]
    assert observed["repeat_candidate_count"] == 0
    assert observed["second"]["attempted"] == 0


def test_a_run_with_nobody_scorable_stops_at_finalise(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """Day 12 section 27: no candidate is a routing outcome, not an error.

    `resolve_targets` reports zero users with an embedded CV, and
    `route_after_targets` sends the run straight to `finalise` without
    touching scoring.
    """

    async def body() -> dict[str, Any]:
        final_state = await _run_graph()
        return {"summary": build_run_summary(final_state)}

    summary = run_with_database(body)["summary"]

    assert summary["users_with_embedded_cv"] == 0
    assert summary["terminal_reason"] == "no_scorable_users"
    assert "score_and_rank" not in summary["stages_attempted"]


def test_low_coverage_keeps_the_run_out_of_the_notify_branch(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The live shape: a high score that must NOT notify.

    Without skills or experience bounds the pair scores well above the
    threshold on location, title and semantic alone -- and is refused,
    because `weight_covered` is 0.50 against a floor of 0.55. This is
    the coverage gate doing the one job it exists for, and it is the
    reason `notify_eligible = 0` on real data is not a bug.
    """

    async def body() -> dict[str, Any]:
        ids = await _seed_everything(with_skills=False)

        from app.services.job_scoring import run_scoring

        scoring = await run_scoring(user_id=ids["user_id"])

        final_state = await _run_graph(user_id=ids["user_id"], dry_run=True)
        summary = build_run_summary(final_state)

        async with session_scope() as session:
            rows = (await session.execute(select(Notification))).scalars().all()
        return {"scoring": scoring, "summary": summary, "notifications": len(rows)}

    observed = run_with_database(body)

    assert observed["scoring"]["score_max"] > 0.7
    assert observed["scoring"]["notify_eligible"] == 0
    assert observed["summary"]["notify_branch"] == "no_qualifying"
    assert observed["notifications"] == 0
