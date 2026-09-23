"""score_and_notify_user -- the in-flight guard and the coalescing rerun.

Pure asyncio, no database: run_scoring and run_notification_delivery are
monkeypatched to controllable stubs, so what is under test here is the
STATE MACHINE (in-flight tracking per user_id, coalescing a trigger that
arrives mid-run into exactly one rerun) rather than scoring or delivery
themselves. Those are covered elsewhere (test_job_scoring.py,
test_notification_delivery.py) and end-to-end against a real database in
tests/integration/test_instant_recommendation.py, which is what proves
the property this file's coalescing tests only gesture at: that running
twice, sequentially, still sends at most once per (user, job).

pytest-asyncio is not installed and must not be (CLAUDE.md section 5).
Every async case here is a plain synchronous function driving a
coroutine with asyncio.run().
"""

from __future__ import annotations

import asyncio

import app.services.notification_delivery as notification_delivery
from app.services.notification_delivery import score_and_notify_user


def _run(coro):
    return asyncio.run(coro)


def _reset_state() -> None:
    """The in-flight/rerun sets are process-global. Each test starts clean
    and each test uses its own user_id(s), so a leaked entry from a
    failed test cannot poison an unrelated later test either."""
    notification_delivery._in_flight_users.clear()
    notification_delivery._rerun_needed_users.clear()


def _patch(monkeypatch, *, score=None, deliver=None):
    async def default_score(*, user_id):
        return None

    async def default_deliver(*, user_id, trigger_source, notifier=None):
        return {"status": "complete", "trigger_source": trigger_source}

    monkeypatch.setattr(notification_delivery, "run_scoring", score or default_score)
    monkeypatch.setattr(
        notification_delivery, "run_notification_delivery", deliver or default_deliver
    )


def test_a_lone_call_runs_scoring_then_delivery_once(monkeypatch) -> None:
    _reset_state()
    calls: list[tuple] = []

    async def fake_score(*, user_id):
        calls.append(("score", user_id))

    async def fake_deliver(*, user_id, trigger_source, notifier=None):
        calls.append(("deliver", user_id, trigger_source))
        return {"status": "complete", "trigger_source": trigger_source}

    _patch(monkeypatch, score=fake_score, deliver=fake_deliver)

    result = _run(score_and_notify_user(7, trigger_source="onboarding"))

    assert calls == [("score", 7), ("deliver", 7, "onboarding")]
    assert result["status"] == "complete"
    assert 7 not in notification_delivery._in_flight_users


def test_rescore_false_skips_scoring_entirely(monkeypatch) -> None:
    _reset_state()
    calls: list[str] = []

    async def fake_score(*, user_id):
        calls.append("score")

    async def fake_deliver(*, user_id, trigger_source, notifier=None):
        calls.append("deliver")
        return {"status": "complete"}

    _patch(monkeypatch, score=fake_score, deliver=fake_deliver)

    _run(score_and_notify_user(7, trigger_source="preferences", rescore=False))

    assert calls == ["deliver"]


def test_a_trigger_that_arrives_mid_run_coalesces_into_exactly_one_rerun(
    monkeypatch,
) -> None:
    """The property Day 16's review asked for by name: two triggers
    during one run -> exactly two runs total, neither one nor three."""
    _reset_state()

    run_started = asyncio.Event()
    release_first_run = asyncio.Event()
    run_count = 0

    async def fake_score(*, user_id):
        nonlocal run_count
        run_count += 1
        if run_count == 1:
            run_started.set()
            await release_first_run.wait()

    _patch(monkeypatch, score=fake_score)

    async def scenario():
        first = asyncio.create_task(score_and_notify_user(7, trigger_source="onboarding"))
        await run_started.wait()

        # A second trigger arrives while the first is still inside
        # run_scoring. It must not start a second, overlapping run.
        second = await score_and_notify_user(7, trigger_source="preferences")
        assert second["status"] == "coalesced"
        assert 7 in notification_delivery._rerun_needed_users

        release_first_run.set()
        await first

    _run(scenario())

    assert run_count == 2, f"expected exactly two runs, got {run_count}"
    assert 7 not in notification_delivery._in_flight_users
    assert 7 not in notification_delivery._rerun_needed_users


def test_a_coalesced_rerun_forces_rescore_even_when_requested_false(
    monkeypatch,
) -> None:
    """A threshold-only edit (rescore=False) arriving mid-run must not
    stop the rerun from rescoring -- by the time it arrives there is no
    cheap way to know a roles/locations edit didn't also land."""
    _reset_state()

    run_started = asyncio.Event()
    release_first_run = asyncio.Event()
    rescore_calls: list[int] = []

    async def fake_score(*, user_id):
        rescore_calls.append(user_id)
        if len(rescore_calls) == 1:
            run_started.set()
            await release_first_run.wait()

    _patch(monkeypatch, score=fake_score)

    async def scenario():
        first = asyncio.create_task(
            score_and_notify_user(7, trigger_source="onboarding", rescore=True)
        )
        await run_started.wait()

        await score_and_notify_user(7, trigger_source="preferences", rescore=False)

        release_first_run.set()
        await first

    _run(scenario())

    assert len(rescore_calls) == 2, "the coalesced rerun must call run_scoring too"


def test_two_different_users_do_not_block_each_other(monkeypatch) -> None:
    _reset_state()
    seen: list[int] = []

    async def fake_score(*, user_id):
        seen.append(user_id)

    _patch(monkeypatch, score=fake_score)

    async def scenario():
        await asyncio.gather(
            score_and_notify_user(1, trigger_source="onboarding"),
            score_and_notify_user(2, trigger_source="preferences"),
        )

    _run(scenario())

    assert sorted(seen) == [1, 2]
    assert notification_delivery._in_flight_users == set()


def test_a_scoring_exception_is_caught_and_never_propagates(monkeypatch) -> None:
    async def fake_score(*, user_id):
        raise RuntimeError("boom")

    async def fake_deliver(*, user_id, trigger_source, notifier=None):
        raise AssertionError("must not be reached if scoring already raised")

    _reset_state()
    _patch(monkeypatch, score=fake_score, deliver=fake_deliver)

    result = _run(score_and_notify_user(7, trigger_source="onboarding"))

    assert result["status"] == "error"
    assert 7 not in notification_delivery._in_flight_users


def test_a_crash_does_not_leave_the_user_permanently_in_flight(monkeypatch) -> None:
    """score_and_notify_user for user X raises inside scoring; a SECOND
    call for user X afterwards must run normally, not be swallowed as
    "in flight". Guaranteed by the finally block at
    app/services/notification_delivery.py:786-787, which discards
    user_id from _in_flight_users unconditionally -- the inner
    try/except (lines 765-779) already prevents run_scoring's exception
    from ever reaching that finally, but this proves the OUTCOME
    (a second call runs for real) rather than trusting the mechanism."""
    _reset_state()

    async def failing_score(*, user_id):
        raise RuntimeError("boom")

    async def unreachable_deliver(*, user_id, trigger_source, notifier=None):
        raise AssertionError("must not be reached if scoring already raised")

    _patch(monkeypatch, score=failing_score, deliver=unreachable_deliver)

    first_result = _run(score_and_notify_user(7, trigger_source="onboarding"))

    assert first_result["status"] == "error"
    assert 7 not in notification_delivery._in_flight_users
    assert 7 not in notification_delivery._rerun_needed_users

    calls: list[tuple] = []

    async def working_score(*, user_id):
        calls.append(("score", user_id))

    async def working_deliver(*, user_id, trigger_source, notifier=None):
        calls.append(("deliver", user_id, trigger_source))
        return {"status": "complete", "trigger_source": trigger_source}

    _patch(monkeypatch, score=working_score, deliver=working_deliver)

    second_result = _run(score_and_notify_user(7, trigger_source="preferences"))

    assert second_result["status"] == "complete", (
        "the second call was swallowed as coalesced/in-flight instead "
        "of running for real"
    )
    assert calls == [("score", 7), ("deliver", 7, "preferences")]


def test_an_invalid_token_failure_never_leaks_the_token_into_logs(
    monkeypatch, caplog
) -> None:
    """The exact CLAUDE.md section 3 leak shape: python-telegram-bot's
    InvalidToken message quotes the rejected token outright ("The token
    `...` was rejected by the server"). A revoked or rotated real token
    reaching score_and_notify_user's except block must never be written
    into the bot's own logs.

    Raised from a monkeypatched run_notification_delivery rather than
    from a real notifier's __aenter__: reaching an actual TelegramNotifier
    would need a real Bot object, and what is under test here is a
    property of score_and_notify_user's own except block (does anything
    between the raise and the log line let the token through), not of
    python-telegram-bot's client -- the same boundary every other test
    in this file mocks at.
    """
    import logging

    from telegram.error import InvalidToken

    _reset_state()
    fake_token = "123456789:AAFakeTokenShapedStringForThisTestOnly"

    async def raising_deliver(*, user_id, trigger_source, notifier=None):
        raise InvalidToken(f"The token `{fake_token}` was rejected by the server")

    _patch(monkeypatch, deliver=raising_deliver)

    with caplog.at_level(logging.ERROR):
        result = _run(score_and_notify_user(7, trigger_source="onboarding"))

    assert result["status"] == "error"
    assert fake_token not in caplog.text
    for record in caplog.records:
        assert fake_token not in record.getMessage()
        assert record.exc_info is None, (
            "exc_info must never be attached here -- it prints str(exc) "
            "verbatim via the traceback, regardless of what the log "
            "message itself says"
        )


def test_notifier_is_forwarded_unchanged_to_run_notification_delivery(monkeypatch) -> None:
    """A test double must be able to reach all the way through, without
    score_and_notify_user importing or constructing a TelegramNotifier
    of its own -- see the module docstring and CLAUDE.md section 4."""
    _reset_state()
    received = {}
    sentinel = object()

    async def fake_deliver(*, user_id, trigger_source, notifier=None):
        received["notifier"] = notifier
        return {"status": "complete"}

    _patch(monkeypatch, deliver=fake_deliver)

    _run(
        score_and_notify_user(
            7, trigger_source="onboarding", rescore=False, notifier=sentinel
        )
    )

    assert received["notifier"] is sentinel
