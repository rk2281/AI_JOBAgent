"""Delivering selected recommendations to Telegram, and counting what happened.

THE SPLIT THAT MATTERS, AND WHY IT IS A SPLIT

    select_notifiable()      applies the gate.  Production only.
    deliver_notifications()  sends a list it is handed.  Knows no gate.
    run_notification_delivery()  = the first, then the second.

`deliver_notifications()` has no `force` flag, no `ignore_gate`
parameter and no way to ask whether a recommendation qualified,
because it never sees a recommendation that has not already been
chosen. That is the entire reason for the split.

The alternative -- one function with a bypass flag, reached from a
script as `run_agent --force` or from config as
`notify_ignore_gate=true` -- puts the ability to message every user
about every job behind a boolean that lives in the production code
path forever. Every reader of that function then has to check which
way the flag was set to know what it does, and one day it is set the
wrong way by a script somebody wrote in a hurry. Here the production
path and the manual path differ in WHICH LIST THEY BUILD, and the code
that talks to Telegram cannot tell them apart or be made to.

    Production:  recommendations -> gate -> eligible -> deliver -> Telegram
    Manual test: recommendations -> a person chooses -> deliver -> Telegram

Both end in the same delivery code. Only one of them contains the gate.

`trigger_source` is what keeps them separable afterwards: without it,
the first hand-sent test message would make "has the production gate
ever actually fired?" unanswerable for good.

THE GATE IS NOT RE-IMPLEMENTED HERE

select_notifiable() calls `is_notify_eligible()` -- the same function
run_scoring calls, not a copy of its three `>=` comparisons. A second
copy would be a second thing to keep in step, and the two would
disagree silently, in the direction of sending messages that should not
have been sent.

That does mean the gate is evaluated twice per pair: once by
run_scoring for its `notify_eligible` counter, once here to choose
rows. They can legitimately differ -- a job retired between the two, or
a user changed their threshold -- so the summary reports BOTH
`notify_eligible` (what scoring counted) and
`notifications_eligible_selected` (what delivery found). A single
number would make a disagreement invisible, and a disagreement is the
most interesting thing this stage can discover.

WHY THE PENDING ROW IS COMMITTED BEFORE THE MESSAGE IS SENT

Same argument as ScoringRunRepository.start() and
AgentRunRepository.start(): a process killed mid-call must leave
evidence rather than silence. The attempt row is written and committed
BEFORE the Telegram call, so a run that dies during the network call
leaves a `pending` row saying what it was doing. Writing it afterwards
would mean the one case most worth having a record of is the one case
with no record.

It also means no database transaction is held open across a network
call, which is a habit worth keeping regardless of this table.

NO ATTEMPT CEILING

Deliberately no `max_attempts`. A Telegram outage must not permanently
cost a user a job -- that is precisely the failure the old
`UniqueConstraint(user_id, job_id)` produced, and adding a retry limit
would reintroduce it with a number attached. Failures accumulate as
rows and are counted in the summary; a human decides when a pattern
means something.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.db.models.recommendation import (
    TRIGGER_SOURCE_SCHEDULED,
    NOTIFICATION_TRIGGER_SOURCES,
)
from app.db.repositories.notification import NotificationRepository
from app.db.repositories.scoring import RecommendationRepository
from app.db.repositories.user import UserRepository
from app.db.session import session_scope
from app.integrations.telegram import TelegramNotifier, describe_telegram_error
from app.services.job_scoring import is_notify_eligible, run_scoring, select_target_user_ids
from app.services.notification_message import format_job_notification
from app.services.replies import BotReply

logger = logging.getLogger(__name__)

# --- statuses ------------------------------------------------------------
#
# Strings, matching every other service in this project, so the graph can
# put one in state without an enum crossing a checkpoint boundary.

STATUS_COMPLETE = "complete"
STATUS_COMPLETE_NO_QUALIFYING = "complete_no_qualifying"
STATUS_PARTIAL = "partial"
STATUS_ALL_FAILED = "all_failed"
STATUS_CONFIGURATION_ERROR = "configuration_error"
STATUS_SKIPPED_DRY_RUN = "skipped_dry_run"

# How many of a user's top recommendations are put in front of the gate.
#
# Safe to bound because the gate's first condition is
# `final_score >= notification_threshold` and the window is ordered by
# final_score DESC: anything the window excludes scores no higher than
# everything it contains. So a row dropped by the window would have been
# dropped by the send cap below it anyway, being the lower-scoring of
# the two. Asserted against the cap at call time rather than left as a
# claim in a comment.
#
# That reasoning only holds if eligibility were monotonic with
# final_score -- it is not. The other two gates, semantic_raw and
# weight_covered, are independent of final_score, so a lower-ranked row
# outside this window can pass all three gates while a higher-ranked row
# inside it fails one of the other two. That means notify_eligible
# (computed over every scored pair, no window -- job_scoring.py) can be
# strictly greater than what select_notifiable() ever finds, for a
# reason app/workflows/state.py's delivery-comment did not list among
# the causes of that disagreement until this line was added.
#
# Known, accepted limitation, not fixed here: widening the window (or
# re-querying past it when fewer than `cap` eligible rows are found) is
# a bigger structural change than this comment should carry. Exposure
# at ~99 jobs/user is judged low. Reconsider once jobs/user grows well
# past current levels -- see CLAUDE.md's Day 14 entry.
_GATE_WINDOW = 25

# UserPreference.notification_threshold's own column default, and what
# run_scoring falls back to for a user with no preference row. Duplicated
# from job_scoring._DEFAULT_NOTIFICATION_THRESHOLD rather than imported
# because that name is private; a divergence would show up in the manual
# script, which prints the threshold it actually used.
_DEFAULT_NOTIFICATION_THRESHOLD = 0.7


def _max_per_user() -> int:
    """How many messages one user may receive from one run.

    A DELIVERY policy, not a gate. The gate decides whether a job is
    worth telling somebody about; this decides that twelve such
    messages at 3am is not a notification, it is a reason to block the
    bot. Read at call time rather than bound at import so a test can
    set it without reloading the module.
    """
    return int(settings.max_notifications_per_user)


@dataclass(frozen=True)
class NotificationCandidate:
    """One recommendation chosen for delivery, flattened to primitives.

    Flattened deliberately. The delivery loop commits between the
    attempt row and the status update, so an ORM instance carried
    across those boundaries would be a detached row waiting to lazy
    load -- the class of bug app/workflows/state.py's "counters, not
    rows" rule exists to avoid, one layer down.

    Carries the three gate inputs alongside the score even though
    delivery never reads them, because the manual test script prints
    them: a person about to send a real message to a real person should
    be able to see WHICH gate a recommendation failed and by how much,
    and re-querying for that would be a second source of the same
    numbers.
    """

    user_id: int
    telegram_id: int
    job_id: int
    recommendation_id: int | None
    final_score: float
    semantic_raw: float | None
    weight_covered: float
    notification_threshold: float
    reply: BotReply


@dataclass
class _Counters:
    """What the delivery loop did. Every field increments inside the loop."""

    eligible_selected: int = 0
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    skipped_duplicate: int = 0
    users_deactivated: int = 0


@dataclass
class GateEvaluation:
    """One recommendation measured against the gate, whether or not it passed.

    Exists for the manual test script. run_scoring counts how many pairs
    pass; nothing until now could say why a particular pair did not, and
    "notify_eligible = 0" with no breakdown is the shape section 0 of
    CLAUDE.md warns about -- a number that looks like an answer.
    """

    candidate: NotificationCandidate
    passes_score: bool
    passes_semantic: bool
    passes_coverage: bool
    already_sent: bool

    @property
    def eligible(self) -> bool:
        return self.passes_score and self.passes_semantic and self.passes_coverage

    def failing_gates(self) -> list[str]:
        """Which gates this row fails, and by how much. Never a bare bool.

        The margin is the useful half. "fails coverage" invites lowering
        the floor; "fails coverage by 0.05" says how far away the whole
        population is, which is a different conversation and the one
        Day 10 Part 4 recorded as needing a decision rather than a
        patch.
        """
        reasons: list[str] = []
        if not self.passes_score:
            gap = self.candidate.notification_threshold - self.candidate.final_score
            reasons.append(
                f"score {self.candidate.final_score:.4f} < "
                f"{self.candidate.notification_threshold:.2f} (short by {gap:.4f})"
            )
        if not self.passes_semantic:
            raw = self.candidate.semantic_raw
            floor = settings.semantic_notify_floor
            shown = "NULL" if raw is None else f"{raw:.4f}"
            gap = "" if raw is None else f" (short by {floor - raw:.4f})"
            reasons.append(f"semantic_raw {shown} < {floor:.2f}{gap}")
        if not self.passes_coverage:
            gap = settings.min_weight_covered_to_notify - self.candidate.weight_covered
            reasons.append(
                f"weight_covered {self.candidate.weight_covered:.4f} < "
                f"{settings.min_weight_covered_to_notify:.2f} (short by {gap:.4f})"
            )
        return reasons


def _build_reply(recommendation, job) -> BotReply:
    """Recommendation + Job -> the message, via the pure formatter."""
    return format_job_notification(
        job_id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        min_experience_years=job.min_experience_years,
        max_experience_years=job.max_experience_years,
        final_score=recommendation.final_score,
        match_reasons=list(recommendation.match_reasons or []),
        url=job.url,
    )


async def evaluate_candidates(
    *, user_id: int, limit: int = _GATE_WINDOW
) -> list[GateEvaluation]:
    """Measure one user's top recommendations against the gate.

    Read-only. Returns every row it looked at, passing or not, so a
    caller can show a person the near misses -- which is the whole
    value of it, since the gate currently passes nothing on real data
    and a bare empty list would say only that.

    Returns [] for a user who does not exist or has been deactivated,
    rather than raising: both are ordinary answers to "who should we
    notify", and neither is this function's business to complain about.
    """
    async with session_scope() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None or not user.is_active:
            return []

        preferences = await UserRepository(session).get_preferences(user_id)
        threshold = (
            preferences.notification_threshold
            if preferences is not None
            else _DEFAULT_NOTIFICATION_THRESHOLD
        )

        pairs = await RecommendationRepository(session).top_with_jobs_for_user(
            user_id, limit
        )
        already_sent = await NotificationRepository(session).sent_job_ids(user_id)

        evaluations: list[GateEvaluation] = []
        for recommendation, job in pairs:
            semantic_raw = recommendation.semantic_raw
            candidate = NotificationCandidate(
                user_id=user_id,
                telegram_id=user.telegram_id,
                job_id=job.id,
                recommendation_id=recommendation.id,
                final_score=recommendation.final_score,
                semantic_raw=semantic_raw,
                weight_covered=recommendation.weight_covered,
                notification_threshold=threshold,
                reply=_build_reply(recommendation, job),
            )
            evaluations.append(
                GateEvaluation(
                    candidate=candidate,
                    passes_score=recommendation.final_score >= threshold,
                    # A NULL semantic_raw abstains and CANNOT pass. It is
                    # not treated as 0.0 and not treated as passing: the
                    # gate exists to say "nothing today was actually any
                    # good", and a row with no similarity at all has not
                    # answered that question.
                    passes_semantic=(
                        semantic_raw is not None
                        and semantic_raw >= settings.semantic_notify_floor
                    ),
                    passes_coverage=(
                        recommendation.weight_covered
                        >= settings.min_weight_covered_to_notify
                    ),
                    already_sent=job.id in already_sent,
                )
            )
        return evaluations


async def select_notifiable(*, user_id: int | None = None) -> list[NotificationCandidate]:
    """The production selection. THIS is where the gate is applied.

    Returns rows that pass all three of `is_notify_eligible()`'s gates
    AND have not already been successfully sent to that user.

    The gate call is the real one. `evaluate_candidates()` above
    computes the same three comparisons locally so it can report each
    one separately, and this function CROSS-CHECKS that local
    evaluation against `is_notify_eligible()` for every row it is about
    to send -- so the two can never drift apart unnoticed. Same tactic
    scripts/notify_reachability_probe.py and scripts/scoring_isolate.py
    use: an independent computation exists to prove the real one means
    what the caller assumes, never to replace it.
    """
    async with session_scope() as session:
        target_ids = await select_target_user_ids(session, user_id)

    selected: list[NotificationCandidate] = []
    cap = _max_per_user()
    assert _GATE_WINDOW >= cap, (
        f"gate window {_GATE_WINDOW} is smaller than the per-user send cap "
        f"{cap}; the window would drop rows the cap would have sent"
    )

    for uid in target_ids:
        evaluations = await evaluate_candidates(user_id=uid)

        chosen: list[NotificationCandidate] = []
        for evaluation in evaluations:
            candidate = evaluation.candidate

            # The cross-check. If these two ever disagree the local
            # breakdown has drifted from the rule that actually decides,
            # and every gate reason printed by the manual script has
            # been describing something other than what happens.
            real = is_notify_eligible(
                final_score=candidate.final_score,
                semantic_raw=candidate.semantic_raw
                if candidate.semantic_raw is not None
                else float("-inf"),
                weight_covered=candidate.weight_covered,
                notification_threshold=candidate.notification_threshold,
            )
            assert real == evaluation.eligible, (
                "notification gate disagreement: is_notify_eligible() says "
                f"{real}, local evaluation says {evaluation.eligible} for "
                f"user={candidate.user_id} job={candidate.job_id}"
            )

            if not real or evaluation.already_sent:
                continue
            chosen.append(candidate)
            if len(chosen) >= cap:
                break

        selected.extend(chosen)

    return selected


async def deliver_notifications(
    candidates: list[NotificationCandidate],
    *,
    trigger_source: str = TRIGGER_SOURCE_SCHEDULED,
    dry_run: bool = False,
    notifier: TelegramNotifier | None = None,
) -> dict:
    """Send an already-chosen list. Applies no gate of its own.

    It cannot apply one: a NotificationCandidate carries no way to ask
    whether it qualified, and this function imports nothing that could
    decide. That is the safety property the module docstring is about.

    `notifier` is injectable so a test can drive the whole loop --
    duplicate detection, failure recording, deactivation -- without a
    bot token or a network. Left at None it builds a real one, so
    nothing in production depends on a caller remembering to pass it.

    Returns a dict of counters, like every other entry point here.
    """
    if trigger_source not in NOTIFICATION_TRIGGER_SOURCES:
        raise ValueError(
            f"unknown trigger_source {trigger_source!r}; "
            f"expected one of {sorted(NOTIFICATION_TRIGGER_SOURCES)}"
        )

    counters = _Counters(eligible_selected=len(candidates))

    if not candidates:
        # Not a failure and not an error. Reported with every counter at
        # 0 rather than as an absence, because zero attempted is a fact
        # this stage established, unlike the None a stage that never ran
        # leaves behind.
        return _result(STATUS_COMPLETE_NO_QUALIFYING, counters, trigger_source)

    if dry_run:
        # Returns BEFORE any attempt row and before any message. The
        # eligible count stands because selection really did happen;
        # everything below it stays 0 because nothing was tried. Same
        # shape as run_scoring's dry run, which computes everything and
        # persists nothing.
        return _result(STATUS_SKIPPED_DRY_RUN, counters, trigger_source)

    owns_notifier = notifier is None
    client = notifier if notifier is not None else TelegramNotifier()

    if owns_notifier:
        await client.__aenter__()
    try:
        deactivated: set[int] = set()

        for candidate in candidates:
            if candidate.user_id in deactivated:
                # This user blocked the bot earlier in this same run.
                # Working through their remaining recommendations would
                # be a guaranteed failure per job and looks like abuse
                # from Telegram's side.
                continue

            attempt_id = await _open_attempt(candidate, trigger_source)
            if attempt_id is None:
                counters.failed += 1
                counters.attempted += 1
                continue

            counters.attempted += 1

            result = await client.send(
                chat_id=candidate.telegram_id, reply=candidate.reply
            )

            if result.configuration_error:
                # Our credentials, not this user. Record the attempt and
                # stop the whole stage rather than marching through every
                # remaining user recording failures that all have one
                # cause -- and emphatically rather than deactivating
                # people because the token is wrong.
                await _mark_failed(attempt_id, result.error or "configuration error")
                counters.failed += 1
                logger.error("Telegram configuration error; stopping delivery")
                return _result(STATUS_CONFIGURATION_ERROR, counters, trigger_source)

            if result.ok:
                sent = await _mark_sent(attempt_id)
                if sent:
                    counters.sent += 1
                else:
                    # The partial unique index refused a second SENT row
                    # for this pair: somebody delivered it between our
                    # check and our write. The message HAS gone out, so
                    # this is not a failure -- it is a duplicate, and the
                    # separate counter is what keeps the two apart.
                    counters.skipped_duplicate += 1
                continue

            await _mark_failed(attempt_id, result.error or "unknown error")
            counters.failed += 1

            if result.user_unreachable:
                await _deactivate(candidate.user_id)
                deactivated.add(candidate.user_id)
                counters.users_deactivated += 1
                logger.warning(
                    "Deactivated user %s: Telegram reports the chat is "
                    "permanently undeliverable",
                    candidate.user_id,
                )
    finally:
        if owns_notifier:
            await client.__aexit__(None, None, None)

    # Observes the WORK, not the plan. All four increment inside the loop
    # above, on the branch actually taken, on the same pass -- so there
    # is no arithmetic path to a wrong breakdown that still sums. What it
    # catches is a fifth outcome added later whose counter somebody
    # forgot to increment, which would otherwise appear as a total
    # quietly larger than its parts.
    #
    # Deliberately NOT asserting eligible_selected == attempted: those
    # differ legitimately whenever a user is deactivated mid-run, and an
    # assertion solved to be true is not a check.
    assert counters.attempted == (
        counters.sent + counters.failed + counters.skipped_duplicate
    ), (
        f"notification funnel does not balance: attempted={counters.attempted} "
        f"sent={counters.sent} failed={counters.failed} "
        f"skipped_duplicate={counters.skipped_duplicate}"
    )

    return _result(_select_status(counters), counters, trigger_source)


def _select_status(counters: _Counters) -> str:
    """The terminal status of one delivery pass. ORDER IS LOAD-BEARING.

    ALL_FAILED is checked before PARTIAL, and both before COMPLETE,
    because each line is a reason the ones below it must not be
    reached. A pass that attempted three messages and sent none must
    never report `complete` on the grounds that it finished the loop --
    that is `complete_no_qualifying` reported one layer down, which is
    the failure this whole project's section 0 is written about.

    all_failed and partial are both already members of
    DEGRADED_SERVICE_STATUSES, so either makes the graph report
    `degraded` and run_agent.py exit non-zero, without anything here
    having to know that.
    """
    if counters.attempted == 0:
        return STATUS_COMPLETE_NO_QUALIFYING
    if counters.sent == 0:
        return STATUS_ALL_FAILED
    if counters.failed > 0:
        return STATUS_PARTIAL
    return STATUS_COMPLETE


def _result(status: str, counters: _Counters, trigger_source: str) -> dict:
    result = {"status": status, "trigger_source": trigger_source}
    result.update(asdict(counters))
    return result


# --- one unit of work each, so a crash leaves the row it already wrote ----


async def _open_attempt(
    candidate: NotificationCandidate, trigger_source: str
) -> int | None:
    """Commit a `pending` row, and return its id.

    Its own transaction, committed before the network call. A run killed
    during the send leaves this row behind saying what it was doing.

    Returns None if the write itself failed -- a database problem is not
    a reason to send an unrecorded message, because an unrecorded send
    is exactly what the duplicate rule cannot protect against later.
    """
    try:
        async with session_scope() as session:
            attempt = await NotificationRepository(session).open_attempt(
                user_id=candidate.user_id,
                job_id=candidate.job_id,
                recommendation_id=candidate.recommendation_id,
                trigger_source=trigger_source,
            )
            return attempt.id
    except Exception:  # noqa: BLE001 - counted as a failure, never fatal
        logger.exception(
            "Could not open a notification attempt for user %s job %s",
            candidate.user_id,
            candidate.job_id,
        )
        return None


async def _mark_sent(attempt_id: int) -> bool:
    """Promote to SENT. False means the partial unique index refused it.

    The IntegrityError is an expected outcome here, not a bug: it is the
    database saying this pair already has a successful delivery. Caught
    narrowly around this one statement so that a genuine database fault
    is not quietly reclassified as a duplicate.
    """
    from sqlalchemy.exc import IntegrityError

    try:
        async with session_scope() as session:
            await NotificationRepository(session).mark_sent(
                attempt_id, datetime.now(timezone.utc)
            )
        return True
    except IntegrityError:
        logger.info(
            "Notification attempt %s was already delivered by another writer",
            attempt_id,
        )
        return False


async def _mark_failed(attempt_id: int, error_message: str) -> None:
    """Record why an attempt did not land.

    `error_message` arrives already redacted from
    describe_telegram_error(). Nothing here formats an exception -- the
    Telegram bot token is in the request URL, so a raw exception written
    to this column is a permanent credential in the database.
    """
    try:
        async with session_scope() as session:
            await NotificationRepository(session).mark_failed(
                attempt_id, error_message
            )
    except Exception:  # noqa: BLE001 - already on the failure path
        logger.exception("Could not record failure for attempt %s", attempt_id)


async def _deactivate(user_id: int) -> None:
    try:
        async with session_scope() as session:
            repository = UserRepository(session)
            user = await repository.get_by_id(user_id)
            if user is not None:
                await repository.deactivate(user)
    except Exception:  # noqa: BLE001 - already on the failure path
        logger.exception("Could not deactivate user %s", user_id)


async def run_notification_delivery(
    *,
    user_id: int | None = None,
    dry_run: bool = False,
    trigger_source: str = TRIGGER_SOURCE_SCHEDULED,
    notifier: TelegramNotifier | None = None,
) -> dict:
    """Entry point. Gate, then deliver, then report.

    Module-level `async def run_x(...)` returning a dict of counters,
    owning its own transactions and committing per unit -- the same
    shape as run_ingestion, run_enrichment, run_job_embedding and
    run_scoring, and for the same reasons.

    This is the ONLY function in the module that both applies the gate
    and talks to Telegram, and it does neither itself: it calls the two
    halves in order. There is no argument that makes it skip the first.

    trigger_source defaults to the nightly value so every existing
    caller (run_agent.py, the graph's notify node) is unaffected. The
    onboarding path is the first caller to pass a different one.

    notifier defaults to None, same as deliver_notifications' own
    parameter -- added so a caller of THIS function (not just of
    deliver_notifications directly) can inject a stub for a test. Every
    existing caller omits it and gets today's behaviour: a real
    TelegramNotifier built inside deliver_notifications.
    """
    candidates = await select_notifiable(user_id=user_id)
    return await deliver_notifications(
        candidates,
        trigger_source=trigger_source,
        dry_run=dry_run,
        notifier=notifier,
    )


# --- instant, onboarding- or preferences-triggered scoring + delivery ----
#
# Both onboarding (a new CV) and /preferences (a changed target_roles,
# preferred_locations, or notification_threshold) want the same thing:
# score (maybe) and notify ONE user right away instead of waiting for
# the next scheduled run_agent.py pass. One implementation, both
# callers -- CLAUDE.md's Day 6 rule about two users of one rule sharing
# one implementation, invoked again here for the Day 16 review.
#
# SILENT AND BEST-EFFORT, BY DESIGN, HERE RATHER THAN AT EACH CALL SITE
#
# Never raises. A failure here costs a user an instant message, not a
# stuck state, because the nightly pass redoes this regardless -- the
# same promise onboarding's own instant path already made before this
# function existed. Putting the try/except HERE instead of in every
# caller means that promise holds for every caller automatically,
# including a future one, rather than depending on each handler
# remembering to wrap its call.
#
# COALESCING, NOT DROPPING
#
# A user who edits target_roles and then, seconds later, preferred_
# locations must not have the second edit silently lost until the next
# 03:00 run. If a call for this user_id arrives while one is already
# running, it does not start a second overlapping run (see IN-FLIGHT
# below) and it does not return without effect either: it sets a
# "run again" flag, and the in-flight run loops once more after it
# finishes, reading whatever preferences are current AT THAT POINT.
# A rerun always forces rescore=True regardless of what the coalesced
# caller asked for: by the time a second trigger arrives there is no
# cheap way to tell whether the field that changed was scoring-relevant,
# and always getting it right is worth one occasionally-redundant
# scoring pass on what is already a rare path.
#
# IN-FLIGHT, NOT A QUEUE, AND IN-PROCESS ONLY
#
# Per-user_id, a plain module-level set -- safe under asyncio's
# single-threaded cooperative scheduling because there is no `await`
# between the membership check and the add, so two coroutines can never
# both see it empty. This serialises onboarding-triggered and
# preferences-triggered runs against EACH OTHER for the same user, not
# just preferences edits against themselves: an onboarding CV upload
# and a preferences edit landing within the same event loop tick for
# the same user now share this exact guard.
#
# It does NOT serialise against the nightly run_agent.py pass, which is
# a separate OS process and cannot see this in-process set. That race
# is real and traced (see notifications.open_attempt: any number of
# PENDING rows are allowed per pair, so two processes can both pass the
# gate and both call TelegramNotifier.send before either commits
# mark_sent) and is deliberately NOT closed here -- see CLAUDE.md's Day
# 16 entry for the precedent (CVRepository.claim_for_extraction's
# atomic conditional UPDATE) and the condition for revisiting it.
_in_flight_users: set[int] = set()
_rerun_needed_users: set[int] = set()


async def score_and_notify_user(
    user_id: int,
    *,
    trigger_source: str,
    rescore: bool = True,
    notifier: TelegramNotifier | None = None,
) -> dict:
    """Score (maybe) and notify one user right away. Never raises.

    rescore controls whether run_scoring() runs first. A threshold-only
    preference edit does not change any stored score -- the gate reads
    notification_threshold fresh at delivery time regardless (see
    evaluate_candidates) -- so rescore=False skips a full-catalog
    scoring pass that could not change anything. A rerun coalesced from
    a second trigger always forces rescore=True; see the module note
    above for why.

    notifier is forwarded to run_notification_delivery unchanged, so a
    test can inject a stub through the whole chain without a bot token
    or a network call, and production leaves it None.
    """
    if user_id in _in_flight_users:
        _rerun_needed_users.add(user_id)
        logger.info(
            "score_and_notify_user already running for user_id=%s; "
            "coalescing into a rerun after it finishes",
            user_id,
        )
        return {"status": "coalesced", "trigger_source": trigger_source}

    _in_flight_users.add(user_id)
    try:
        result: dict = {"status": "not_run", "trigger_source": trigger_source}
        while True:
            try:
                if rescore:
                    await run_scoring(user_id=user_id)
                result = await run_notification_delivery(
                    user_id=user_id,
                    trigger_source=trigger_source,
                    notifier=notifier,
                )
            except Exception as exc:  # noqa: BLE001 - best-effort; the nightly pass is the real path
                # NEVER logger.exception() here. That call attaches
                # exc_info -- the full traceback, which prints str(exc)
                # verbatim -- and python-telegram-bot's InvalidToken
                # message quotes the rejected token outright ("The
                # token `...` was rejected by the server"). A revoked
                # or rotated real token reaching this except block would
                # otherwise be written into the bot's own logs, the same
                # leak shape CLAUDE.md section 3 already names: the
                # credential travels in something that doesn't look like
                # a secret operation. describe_telegram_error() is safe
                # for ANY exception, not just Telegram ones -- its last
                # tier is "class name only" -- so it is called
                # unconditionally rather than guessing whether this
                # failure came from Telegram.
                logger.error(
                    "score_and_notify_user failed for user_id=%s "
                    "trigger_source=%s exception_type=%s telegram_detail=%s",
                    user_id,
                    trigger_source,
                    type(exc).__name__,
                    describe_telegram_error(exc),
                )
                result = {"status": "error", "trigger_source": trigger_source}

            if user_id in _rerun_needed_users:
                _rerun_needed_users.discard(user_id)
                rescore = True
                continue
            break
    finally:
        _in_flight_users.discard(user_id)

    return result
