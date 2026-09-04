"""The delivery loop: every outcome it can record, and the ones it must not.

No database and no bot token. The four helpers that own transactions
(_open_attempt, _mark_sent, _mark_failed, _deactivate) are replaced with
fakes that record what they were asked to do, so the LOOP's decisions
are what is under test here. The SQL those helpers wrap -- the partial
unique index in particular -- is covered against real PostgreSQL in
tests/test_notification_constraints.py, because a unique index is
exactly the thing a fake cannot prove.

pytest-asyncio is not installed and must not be. Async cases are plain
synchronous functions driving a coroutine with asyncio.run().

THE PROPERTY MOST OF THIS FILE IS ABOUT

`sent` must count DELIVERIES, not attempts. A counter incremented
before the send, or on the wrong branch, would let a completely broken
Telegram report a healthy run -- which is this project's section 0
failure in its most literal form. Several tests below assert a zero
that a plausible implementation would report as non-zero.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.models.recommendation import (
    TRIGGER_SOURCE_MANUAL_TEST,
    TRIGGER_SOURCE_SCHEDULED,
)
from app.integrations.telegram import SendResult
from app.services import notification_delivery as delivery
from app.services.notification_delivery import (
    NotificationCandidate,
    deliver_notifications,
)
from app.services.replies import BotReply, Button


def _run(coro):
    return asyncio.run(coro)


def _candidate(*, user_id=2, job_id=10, telegram_id=555, score=0.9):
    return NotificationCandidate(
        user_id=user_id,
        telegram_id=telegram_id,
        job_id=job_id,
        recommendation_id=job_id * 100,
        final_score=score,
        semantic_raw=0.7,
        weight_covered=1.0,
        notification_threshold=0.7,
        reply=BotReply(text="job", buttons=[[Button("Interested", "fb:interested:1")]]),
    )


class _FakeNotifier:
    """Returns a scripted SendResult per call, and records the sends."""

    def __init__(self, results: list[SendResult]) -> None:
        self._results = list(results)
        self.sent: list[tuple[int, str]] = []

    async def send(self, *, chat_id, reply):
        self.sent.append((chat_id, reply.text))
        if self._results:
            return self._results.pop(0)
        return SendResult(ok=True)


class _Recorder:
    """Stands in for the four transaction-owning helpers."""

    def __init__(self, *, mark_sent_ok=True, open_fails=False) -> None:
        self.opened: list[tuple[int, int, str]] = []
        self.marked_sent: list[int] = []
        self.marked_failed: list[tuple[int, str]] = []
        self.deactivated: list[int] = []
        self._mark_sent_ok = mark_sent_ok
        self._open_fails = open_fails
        self._next_id = 1

    def install(self, monkeypatch) -> None:
        async def open_attempt(candidate, trigger_source):
            if self._open_fails:
                return None
            self.opened.append(
                (candidate.user_id, candidate.job_id, trigger_source)
            )
            attempt_id = self._next_id
            self._next_id += 1
            return attempt_id

        async def mark_sent(attempt_id):
            self.marked_sent.append(attempt_id)
            return self._mark_sent_ok

        async def mark_failed(attempt_id, error_message):
            self.marked_failed.append((attempt_id, error_message))

        async def deactivate(user_id):
            self.deactivated.append(user_id)

        monkeypatch.setattr(delivery, "_open_attempt", open_attempt)
        monkeypatch.setattr(delivery, "_mark_sent", mark_sent)
        monkeypatch.setattr(delivery, "_mark_failed", mark_failed)
        monkeypatch.setattr(delivery, "_deactivate", deactivate)


def _deliver(candidates, notifier, monkeypatch, *, recorder=None, **kwargs):
    recorder = recorder or _Recorder()
    recorder.install(monkeypatch)
    result = _run(
        deliver_notifications(candidates, notifier=notifier, **kwargs)
    )
    return result, recorder


# --- the empty and rehearsal cases ---------------------------------------


def test_an_empty_list_is_not_a_failure_and_sends_nothing(monkeypatch) -> None:
    notifier = _FakeNotifier([])
    result, recorder = _deliver([], notifier, monkeypatch)

    assert result["status"] == "complete_no_qualifying"
    assert result["attempted"] == 0
    assert result["sent"] == 0
    assert result["eligible_selected"] == 0
    assert notifier.sent == []
    assert recorder.opened == []


def test_a_dry_run_selects_but_writes_and_sends_nothing(monkeypatch) -> None:
    """The eligible count survives because selection really happened;
    everything below it stays 0 because nothing was tried. Same shape as
    run_scoring's dry run, which computes everything and persists
    nothing."""
    notifier = _FakeNotifier([])
    result, recorder = _deliver(
        [_candidate(), _candidate(job_id=11)], notifier, monkeypatch, dry_run=True
    )

    assert result["status"] == "skipped_dry_run"
    assert result["eligible_selected"] == 2
    assert result["attempted"] == 0
    assert result["sent"] == 0
    assert notifier.sent == [], "a dry run must not send a Telegram message"
    assert recorder.opened == [], "a dry run must not write an attempt row"


# --- the happy path ------------------------------------------------------


def test_a_successful_send_is_recorded_as_sent(monkeypatch) -> None:
    notifier = _FakeNotifier([SendResult(ok=True)])
    result, recorder = _deliver([_candidate()], notifier, monkeypatch)

    assert result["status"] == "complete"
    assert result["attempted"] == 1
    assert result["sent"] == 1
    assert result["failed"] == 0
    assert len(notifier.sent) == 1
    assert recorder.marked_sent == [1]
    assert recorder.marked_failed == []


def test_the_attempt_row_is_opened_before_the_message_is_sent(monkeypatch) -> None:
    """A process killed during the network call must leave evidence
    rather than silence -- the same argument ScoringRunRepository.start()
    and AgentRunRepository.start() both make.

    Asserted by having the notifier check the recorder mid-flight, which
    is the only way to observe an ordering rather than a final state.
    """
    recorder = _Recorder()

    class _OrderCheckingNotifier(_FakeNotifier):
        async def send(self, *, chat_id, reply):
            assert recorder.opened, "attempt row must exist before the send"
            assert not recorder.marked_sent, "status must not be set before the send"
            return await super().send(chat_id=chat_id, reply=reply)

    notifier = _OrderCheckingNotifier([SendResult(ok=True)])
    result, _ = _deliver([_candidate()], notifier, monkeypatch, recorder=recorder)

    assert result["sent"] == 1


# --- failure, and retry ---------------------------------------------------


def test_a_telegram_failure_is_recorded_and_does_not_raise(monkeypatch) -> None:
    """One undeliverable job must not end a run."""
    notifier = _FakeNotifier([SendResult(ok=False, error="connection failed (TimedOut)")])
    result, recorder = _deliver([_candidate()], notifier, monkeypatch)

    assert result["status"] == "all_failed"
    assert result["attempted"] == 1
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert recorder.marked_failed == [(1, "connection failed (TimedOut)")]


def test_one_failure_does_not_stop_the_next_candidate(monkeypatch) -> None:
    """The property that makes a run survivable. A transient error on
    job 10 must not cost job 11 its notification."""
    notifier = _FakeNotifier(
        [SendResult(ok=False, error="timed out"), SendResult(ok=True)]
    )
    result, recorder = _deliver(
        [_candidate(job_id=10), _candidate(job_id=11)], notifier, monkeypatch
    )

    assert result["status"] == "partial"
    assert result["attempted"] == 2
    assert result["sent"] == 1
    assert result["failed"] == 1
    assert len(notifier.sent) == 2


def test_a_failed_attempt_can_be_retried_later(monkeypatch) -> None:
    """failed -> failed -> sent is a legal history.

    Nothing in the loop consults an attempt count, and there is no
    ceiling to consult. A Telegram outage must not permanently cost a
    user a job, which is precisely what the old
    UniqueConstraint(user_id, job_id) did.
    """
    candidate = _candidate()

    first, _ = _deliver(
        [candidate], _FakeNotifier([SendResult(ok=False, error="timed out")]), monkeypatch
    )
    second, _ = _deliver(
        [candidate], _FakeNotifier([SendResult(ok=False, error="timed out")]), monkeypatch
    )
    third, recorder = _deliver(
        [candidate], _FakeNotifier([SendResult(ok=True)]), monkeypatch
    )

    assert first["failed"] == 1 and first["sent"] == 0
    assert second["failed"] == 1 and second["sent"] == 0
    assert third["sent"] == 1 and third["failed"] == 0
    assert recorder.opened, "the third attempt still opened a row"


def test_a_database_failure_opening_the_attempt_counts_as_a_failure(
    monkeypatch,
) -> None:
    """And crucially sends nothing. An unrecorded send is exactly what
    the duplicate rule cannot protect against afterwards."""
    notifier = _FakeNotifier([SendResult(ok=True)])
    recorder = _Recorder(open_fails=True)
    result, _ = _deliver([_candidate()], notifier, monkeypatch, recorder=recorder)

    assert result["failed"] == 1
    assert result["sent"] == 0
    assert notifier.sent == [], "no message may be sent without a recorded attempt"


# --- duplicates ----------------------------------------------------------


def test_a_refused_sent_row_is_counted_as_duplicate_not_failure(monkeypatch) -> None:
    """The partial unique index refused a second SENT row, meaning
    somebody delivered this pair between our check and our write.

    The message HAS gone out, so this is not a failure -- and counting
    it as one would make a healthy race look like a broken Telegram.
    """
    notifier = _FakeNotifier([SendResult(ok=True)])
    recorder = _Recorder(mark_sent_ok=False)
    result, _ = _deliver([_candidate()], notifier, monkeypatch, recorder=recorder)

    assert result["skipped_duplicate"] == 1
    assert result["sent"] == 0
    assert result["failed"] == 0
    assert result["attempted"] == 1


# --- the blocked user ----------------------------------------------------


def test_a_blocked_user_is_deactivated(monkeypatch) -> None:
    notifier = _FakeNotifier(
        [SendResult(ok=False, error="Forbidden", user_unreachable=True)]
    )
    result, recorder = _deliver([_candidate(user_id=2)], notifier, monkeypatch)

    assert recorder.deactivated == [2]
    assert result["users_deactivated"] == 1
    assert result["failed"] == 1


def test_a_blocked_users_remaining_jobs_are_not_attempted(monkeypatch) -> None:
    """Working through the rest of their recommendations would be a
    guaranteed failure per job, and looks like abuse from Telegram's
    side."""
    notifier = _FakeNotifier(
        [SendResult(ok=False, error="Forbidden", user_unreachable=True)]
    )
    result, recorder = _deliver(
        [
            _candidate(user_id=2, job_id=10),
            _candidate(user_id=2, job_id=11),
            _candidate(user_id=2, job_id=12),
        ],
        notifier,
        monkeypatch,
    )

    assert len(notifier.sent) == 1, "only the first send is attempted"
    assert result["attempted"] == 1
    assert result["users_deactivated"] == 1


def test_blocking_one_user_does_not_suppress_another(monkeypatch) -> None:
    """The deactivation must be scoped to the user, not to the run."""
    notifier = _FakeNotifier(
        [
            SendResult(ok=False, error="Forbidden", user_unreachable=True),
            SendResult(ok=True),
        ]
    )
    result, recorder = _deliver(
        [_candidate(user_id=2, job_id=10), _candidate(user_id=3, job_id=11)],
        notifier,
        monkeypatch,
    )

    assert recorder.deactivated == [2]
    assert result["sent"] == 1
    assert result["users_deactivated"] == 1


def test_a_transient_failure_never_deactivates_anybody(monkeypatch) -> None:
    """The expensive mistake in the other direction: a deactivated user
    silently stops receiving every future notification."""
    notifier = _FakeNotifier([SendResult(ok=False, error="timed out")])
    result, recorder = _deliver([_candidate()], notifier, monkeypatch)

    assert recorder.deactivated == []
    assert result["users_deactivated"] == 0


# --- our own credentials -------------------------------------------------


def test_a_configuration_error_stops_the_run_and_blames_nobody(monkeypatch) -> None:
    """A wrong token fails for every user in sequence. Marching through
    the table recording failures that all have one cause is noise; doing
    it while deactivating people would be a catastrophe."""
    notifier = _FakeNotifier(
        [SendResult(ok=False, error="invalid bot token", configuration_error=True)]
    )
    result, recorder = _deliver(
        [_candidate(job_id=10), _candidate(job_id=11)], notifier, monkeypatch
    )

    assert result["status"] == "configuration_error"
    assert len(notifier.sent) == 1, "it stops rather than trying the second"
    assert recorder.deactivated == [], "nobody is blamed for our token"
    assert result["failed"] == 1


def test_a_configuration_error_is_a_degraded_status() -> None:
    """So the graph reports `degraded` and run_agent.py exits non-zero
    rather than a scheduled run reporting success having sent nothing."""
    from app.workflows.state import DEGRADED_SERVICE_STATUSES

    assert "configuration_error" in DEGRADED_SERVICE_STATUSES
    assert "all_failed" in DEGRADED_SERVICE_STATUSES
    assert "partial" in DEGRADED_SERVICE_STATUSES


# --- trigger source ------------------------------------------------------


def test_the_scheduled_trigger_source_reaches_the_row(monkeypatch) -> None:
    notifier = _FakeNotifier([SendResult(ok=True)])
    result, recorder = _deliver(
        [_candidate()], notifier, monkeypatch, trigger_source=TRIGGER_SOURCE_SCHEDULED
    )

    assert result["trigger_source"] == "scheduled"
    assert recorder.opened[0][2] == "scheduled"


def test_the_manual_test_trigger_source_reaches_the_row(monkeypatch) -> None:
    """Without this column, the first hand-sent message would make "has
    the production gate ever actually fired?" unanswerable for the life
    of the table."""
    notifier = _FakeNotifier([SendResult(ok=True)])
    result, recorder = _deliver(
        [_candidate()], notifier, monkeypatch, trigger_source=TRIGGER_SOURCE_MANUAL_TEST
    )

    assert result["trigger_source"] == "manual_test"
    assert recorder.opened[0][2] == "manual_test"


def test_an_unknown_trigger_source_is_refused(monkeypatch) -> None:
    """A typo would otherwise write an unrecognised provenance that no
    later query filters on, quietly excluding those rows from both
    'scheduled' and 'manual_test' counts."""
    _Recorder().install(monkeypatch)

    with pytest.raises(ValueError, match="trigger_source"):
        _run(
            deliver_notifications(
                [_candidate()],
                trigger_source="whatever",
                notifier=_FakeNotifier([SendResult(ok=True)]),
            )
        )


def test_the_default_trigger_source_is_scheduled(monkeypatch) -> None:
    """The production value is the default, so a caller that forgets is
    correct rather than unrecorded."""
    notifier = _FakeNotifier([SendResult(ok=True)])
    result, _ = _deliver([_candidate()], notifier, monkeypatch)

    assert result["trigger_source"] == "scheduled"


# --- the funnel ----------------------------------------------------------


def test_the_funnel_balances_across_every_outcome(monkeypatch) -> None:
    """attempted == sent + failed + skipped_duplicate.

    All four increment inside the loop on the branch actually taken, so
    there is no arithmetic path to a wrong breakdown that still sums.
    What it catches is a fifth outcome added later whose counter
    somebody forgot to increment.
    """
    notifier = _FakeNotifier(
        [
            SendResult(ok=True),
            SendResult(ok=False, error="timed out"),
            SendResult(ok=True),
        ]
    )
    result, _ = _deliver(
        [_candidate(job_id=j) for j in (10, 11, 12)], notifier, monkeypatch
    )

    assert result["attempted"] == (
        result["sent"] + result["failed"] + result["skipped_duplicate"]
    )
    assert result["attempted"] == 3


def test_eligible_selected_is_not_forced_to_equal_attempted(monkeypatch) -> None:
    """They differ legitimately -- a user deactivated mid-run leaves
    rows selected and never attempted -- and an assertion solved to be
    true is not a check."""
    notifier = _FakeNotifier(
        [SendResult(ok=False, error="Forbidden", user_unreachable=True)]
    )
    result, _ = _deliver(
        [_candidate(job_id=10), _candidate(job_id=11)], notifier, monkeypatch
    )

    assert result["eligible_selected"] == 2
    assert result["attempted"] == 1


# --- the safety property this whole design exists for --------------------


def test_the_delivery_function_has_no_way_to_bypass_a_gate() -> None:
    """The reason production and the manual script are two callers of one
    function rather than one function with a flag.

    A `force`, `ignore_gate` or `skip_gate` parameter here would put the
    ability to message every user about every job into the production
    code path permanently. Asserted against the signature so that adding
    one is a test failure rather than a code review someone was busy for.
    """
    import inspect

    parameters = set(inspect.signature(deliver_notifications).parameters)

    for forbidden in ("force", "ignore_gate", "skip_gate", "bypass_gate"):
        assert forbidden not in parameters

    assert parameters == {
        "candidates",
        "trigger_source",
        "dry_run",
        "notifier",
    }
