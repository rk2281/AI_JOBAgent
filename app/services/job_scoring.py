"""Score every scorable job against every scorable user, traceably.

This module has exactly two things it must never do, and they are the
same two things app/services/scoring.py must never do, because this
is the file that actually calls it: score a missing signal as zero,
and produce a number nobody can trace back to its inputs. Everything
below exists to feed combine() honest inputs and to record enough
about the run that a bad number can be explained rather than merely
distrusted.

Owns its own transactions, like run_ingestion and run_enrichment.
Unlike either of those, nothing here calls an external API -- both
sides of every comparison are already vectors sitting in Postgres, so
there is no quota to ration and no reason to pace calls. What still
matters is committing per USER rather than in one giant transaction,
so an interrupted run keeps the users it already finished instead of
losing them.

Cosine similarity is computed by JobRepository.nearest_to(), the same
method and the same `<=>` operator Day 7's HNSW index was built for --
never reimplemented in Python. Computing it a second way would produce
two numbers that are supposed to agree, with no test that they do, and
the entire value of a pgvector index is that the database engine's
answer IS the answer.

The status a run ends with matters as much as its numbers, because
three of the possible outcomes look identical to a healthy run unless
something is specifically checking for them:

  ALL_ABSTAINED is a full funnel with every column balanced, every
  pair written, and every one of them arithmetic performed on no
  data -- because job_skills or the experience columns are empty
  across the board. Nothing about the shape of the run says this
  happened; only the abstain counters do.

  DEGENERATE is 99 rows written, status otherwise indistinguishable
  from success, and a ranking that carries no information because
  every pair landed on the same final_score.

  COMPLETE_NO_QUALIFYING is the one status here that is HEALTHY and
  must read that way at a glance: scoring ran correctly, every signal
  had data, and nothing happened to be a good enough match today. It
  is the direct counterpart of embedding_runs.NOTHING_TO_DO and
  ingestion_runs.NO_RESULTS -- "worked, and found nothing" is not the
  same as "did not work."
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.job import Job
from app.db.models.profile import Profile
from app.db.models.scoring import ScoringStatus
from app.db.repositories.cv import CVRepository
from app.db.repositories.job import JobRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.scoring import RecommendationRepository, ScoringRunRepository
from app.db.repositories.user import UserRepository
from app.db.session import session_scope
from app.services.scoring import ScoredPair, assess_quality, combine, compute_inputs_fingerprint, rank
from app.services.scoring_signals import (
    score_experience,
    score_location,
    score_semantic,
    score_skill,
    score_title,
    semantic_clamp_flags,
)

# Above pgvector's default of 40, for the same reason job_search.py
# raises it: the default keeps only 40 candidates in flight while
# descending the HNSW graph, and asking for more results than that
# starts costing recall. Scoring asks for every scorable job, which is
# routinely more than 40, so ef_search must scale with that count
# rather than sit at the default meant for a top-10 search.
_MIN_EF_SEARCH = 64

# UserPreference defaults used when a scorable user has no preference
# row at all. Chosen to match UserPreference's own column defaults
# rather than inventing separate ones -- a user who has not set
# preferences should score exactly as an onboarded user who accepted
# every default would.
_DEFAULT_NOTIFICATION_THRESHOLD = 0.7


@dataclass
class _Counters:
    """A funnel, not a summary. See app/db/models/scoring.py for the
    two equalities the service asserts before returning."""

    users_considered: int = 0

    # Kept under this name deliberately, even though it counts three
    # things and only one of them is literally "no CV". Renaming it
    # would make every scoring_runs row written before the breakdown
    # existed unreadable against every row written after -- a column
    # that means one thing up to a date and another thing afterwards is
    # worse than a column with an imprecise name. The three counters
    # below say which cause applied; this one stays comparable.
    users_skipped_no_cv: int = 0

    # The breakdown. Sums to users_skipped_no_cv, asserted after the
    # loop. no_profile means the user never got that far; no_active_cv
    # means a profile with no active version; cv_not_embedded is the
    # only one of the three that running the embedding pass would fix.
    users_skipped_no_profile: int = 0
    users_skipped_no_active_cv: int = 0
    users_skipped_cv_not_embedded: int = 0

    users_scored: int = 0

    jobs_considered: int = 0
    jobs_skipped_no_embedding: int = 0
    jobs_excluded_manual: int = 0
    jobs_scored: int = 0

    pairs_scored: int = 0

    # Two separate numbers for two separate causes, because they are
    # not the same event and folding them into one silent `continue`
    # is what let the limit bug hide. nearest_dropped_excluded is
    # expected and should equal the number of excluded rows that are
    # active and embedded. nearest_dropped_unscorable is expected to
    # be ZERO: it can only be non-zero if list_scorable_jobs() and
    # nearest_to(), read in two different sessions, disagree about
    # which jobs exist -- a row exclusion or an embedding changing
    # underneath the run. If it is ever non-zero, that is a finding
    # about concurrency, not a rounding detail.
    nearest_dropped_excluded: int = 0
    nearest_dropped_unscorable: int = 0

    abstain_semantic: int = 0
    abstain_skill: int = 0
    abstain_experience: int = 0
    abstain_location: int = 0
    abstain_title: int = 0

    semantic_clamped_low: int = 0
    semantic_clamped_high: int = 0

    quality_penalty_agency: int = 0
    quality_penalty_no_city: int = 0

    jobs_remote: int = 0
    jobs_hybrid: int = 0

    notify_eligible: int = 0


def is_notify_eligible(
    *,
    final_score: float,
    semantic_raw: float,
    weight_covered: float,
    notification_threshold: float,
) -> bool:
    """The three notification gates, all inclusive.

    Three gates, not one, and each compared with `>=` so the boundary
    value QUALIFIES. Day 6's `median < 500` stayed silent when the
    median was exactly 500: the boundary is the case that fails while
    looking like it should pass, so all three are tested at exactly
    their floors rather than near them.

    The third gate is the one that is easy to leave out. Renormalising
    by weight_covered means a notification_threshold of 0.7 does not
    mean the same thing for a job scored on 35% of the weight as for
    one scored on 100% -- the first is a confident-looking number
    computed from almost nothing. min_weight_covered_to_notify is what
    stops a data gap from being read as a good match.

    Extracted from run_scoring() so it can be tested at its exact
    boundary values without a database. The caller is unchanged.
    """
    return (
        final_score >= notification_threshold
        and semantic_raw >= settings.semantic_notify_floor
        and weight_covered >= settings.min_weight_covered_to_notify
    )


def select_status(
    *,
    jobs_scored: int,
    users_scored: int,
    pairs_scored: int,
    distinct_score_count: int,
    notify_eligible: int,
    all_signals_abstained_everywhere: bool,
) -> ScoringStatus:
    """Pick the terminal status for a scoring run.

    ORDER IS LOAD-BEARING and is the reason this is a function rather
    than a chain left inline.

    ALL_ABSTAINED must be checked BEFORE DEGENERATE. A run where every
    signal abstained produces weight_covered == 0 on every pair, hence
    final_score == 0.0 on every pair, hence distinct_score_count == 1.
    It satisfies the DEGENERATE condition perfectly. If DEGENERATE were
    checked first, the exact failure this enum was built to name would
    be reported under the wrong name -- and "the ranking carries no
    information" would be recorded where "the model had no data at all"
    actually happened.

    The `pairs_scored > 1` guard on DEGENERATE exists because one pair
    trivially has one distinct score. A single-pair run is not a
    degenerate ranking; it is a ranking of one.

    Extracted from run_scoring() so the whole table can be tested
    without a database. Behaviour is unchanged.
    """
    if jobs_scored == 0:
        return ScoringStatus.NO_CANDIDATE_JOBS
    if users_scored == 0:
        return ScoringStatus.NO_SCORABLE_USERS
    if all_signals_abstained_everywhere:
        return ScoringStatus.ALL_ABSTAINED
    if pairs_scored > 1 and distinct_score_count == 1:
        return ScoringStatus.DEGENERATE
    if notify_eligible > 0:
        return ScoringStatus.COMPLETE
    return ScoringStatus.COMPLETE_NO_QUALIFYING


# The three reasons a user is not scorable. Strings rather than an enum
# because these travel into a JSONB counters column and out to a
# script's stdout; an enum would be re-serialised at both ends, and the
# value stored in Postgres would be a different object from the value a
# test compares against.
SKIP_NO_PROFILE = "no_profile"
SKIP_NO_ACTIVE_CV = "no_active_cv"
SKIP_CV_NOT_EMBEDDED = "cv_not_embedded"


def is_scorable_user(*, profile_present: bool, embedded_version_present: bool) -> bool:
    """Whether one user can be scored at all.

    Trivial on purpose. Its value is not the expression but the fact
    that there is exactly ONE of it: run_scoring's loop and
    resolve_scoring_targets() both call this, so the graph can never
    stop on a definition of "scorable" that differs from the one
    scoring actually applies. Two independent expressions of this rule
    would agree on the day they were written and drift the first time
    either moved -- and the drift would be invisible, because both
    sides would still produce a plausible count.

    Note what this CANNOT distinguish, because the distinction is
    already gone by the time it is called: a user with no active CV
    version and a user whose active version has no embedding both
    arrive here as embedded_version_present=False, since
    active_version_with_embedding() filters both in SQL. run_scoring
    folds them into one users_skipped_no_cv for the same reason. Only
    the second is fixable by running the embedding pass, so that
    conflation is a known open issue, not a decision -- see the Day 9
    record. Do not "fix" it here by splitting this predicate; that
    would make the graph's definition differ from scoring's, which is
    exactly what this function exists to prevent.

    Extracted so the whole truth table can be tested without a
    database, the same way is_notify_eligible() and select_status()
    were. The callers are unchanged.
    """
    return profile_present and embedded_version_present


def classify_skip_reason(
    *,
    profile_present: bool,
    active_version_present: bool,
    embedded_version_present: bool,
) -> str:
    """Which of the three causes made this user unscorable.

    CALLED ONLY AFTER is_scorable_user() HAS RETURNED FALSE. That is
    what makes this structurally incapable of changing who gets scored:
    the gate above is still a function of exactly two booleans, this
    reads a third, and nothing here feeds back into the decision. It is
    reporting layered on top of a rule, not a second rule.

    Most fundamental cause first, and the order is the whole point. A
    user with no profile row also has no active version and no embedded
    version, so all three flags are False and every branch below would
    match. `no_profile` is the one that sends somebody to fix the right
    thing -- telling them the CV is not embedded when the user never
    completed onboarding would point at the wrong stage entirely.

    Asserts rather than returning a fallback when handed a scorable
    user. A caller reaching this with one means the branch above it is
    wrong, and a plausible-looking string is exactly how that would go
    unnoticed: the counters would still sum, the funnel would still
    balance, and one scorable user would be reported as skipped for a
    reason that never happened.
    """
    assert not (profile_present and embedded_version_present), (
        "classify_skip_reason called on a scorable user; "
        "the is_scorable_user branch above it is wrong"
    )

    if not profile_present:
        return SKIP_NO_PROFILE
    if not active_version_present:
        return SKIP_NO_ACTIVE_CV
    return SKIP_CV_NOT_EMBEDDED


async def select_target_user_ids(session, user_id: int | None) -> list[int]:
    """Which users to score: one, or everyone with a profile.

    A direct query against Profile rather than a ProfileRepository
    method, because "every user id that has a profile" is a listing
    query this run needs and no other caller of ProfileRepository
    does -- adding it there would grow that repository for a single
    consumer.

    Public rather than private because resolve_scoring_targets() needs
    the same list, and a second query for "users with a profile" is a
    second place for that definition to live.
    """
    if user_id is not None:
        return [user_id]

    result = await session.execute(select(Profile.user_id))
    return [row[0] for row in result.all()]


async def resolve_scoring_targets(*, user_id: int | None = None) -> dict:
    """How many users could be scored right now, and which.

    Exists so a caller can find out whether there is anything worth
    running BEFORE spending an ingestion pass or a day's enrichment
    quota on a run that would score nobody. run_scoring answers the
    same question, but only after doing all of the work.

    Owns its own session, like run_ingestion and run_scoring, and takes
    none -- so no session, no ORM instance and no lazy relationship
    ever crosses back to the caller. Everything returned is an int or a
    list of ints, which is what makes it safe to put in a checkpointed
    workflow state.

    Deliberately does NOT read preferences. A user with no preference
    row is still scored, using defaults (see run_scoring); consulting
    preferences here would imply a gate that does not exist.

    This re-reads profiles and CV versions that run_scoring will read
    again moments later. That duplicate read is accepted rather than
    cached: holding the rows would mean carrying ORM objects across a
    boundary, and the cost is a handful of queries over a table with
    three rows in it.
    """
    async with session_scope() as session:
        target_ids = await select_target_user_ids(session, user_id)

        profile_repository = ProfileRepository(session)
        cv_repository = CVRepository(session)

        users_with_profile = 0
        scorable_ids: list[int] = []

        for uid in target_ids:
            profile = await profile_repository.get_by_user_id(uid)
            if profile is not None:
                users_with_profile += 1

            version = None
            if profile is not None:
                version = await cv_repository.active_version_with_embedding(uid)

            if is_scorable_user(
                profile_present=profile is not None,
                embedded_version_present=version is not None,
            ):
                scorable_ids.append(uid)

    return {
        "requested_user_id": user_id,
        "users_considered": len(target_ids),
        "users_with_profile": users_with_profile,
        "users_with_embedded_cv": len(scorable_ids),
        "target_user_ids": scorable_ids,
    }


async def run_scoring(
    *,
    user_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Score `user_id`, or every user with a profile, against every
    scorable job.

    dry_run runs the FULL computation -- every signal, every counter,
    every funnel check -- but writes neither a scoring_runs row nor
    any recommendations rows. distinct_score_count and the
    score/semantic_raw min-max-median are computed from the pairs
    still held in memory rather than queried back from the database,
    so a dry run reports the same numbers a real run would without
    needing a persisted scoring_run_id to query against.

    Reports are computed once, before any user is scored, from
    independent counts (count_active_jobs, the two scorable-count
    queries, and a direct count of active+excluded jobs) rather than
    derived from each other by subtraction. That is what makes the
    funnel assertion below a real check instead of an equation solved
    to make itself true: if list_scorable_jobs() and the independent
    exclusion count ever disagree about which jobs are excluded, the
    assertion is what notices.
    """
    weights_version = settings.weights_version
    started_at = datetime.now(timezone.utc)

    counters = _Counters()
    semantic_raw_values: list[float] = []
    all_final_scores: list[float] = []

    async with session_scope() as session:
        job_repository = JobRepository(session)
        counters.jobs_considered = await job_repository.count_active_jobs()
        counters.jobs_skipped_no_embedding = (
            await job_repository.count_active_missing_embedding_scorable()
        )
        # The pool nearest_to() searches, which is NOT the same set as
        # scorable_jobs: it includes excluded rows. Read here, in the
        # same session as the counts above, so all four numbers
        # describe one moment.
        nearest_pool_size = await job_repository.count_active_embedded_jobs()
        scorable_jobs = await job_repository.list_scorable_jobs()

        excluded_result = await session.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.is_active.is_(True), Job.is_excluded.is_(True))
        )
        counters.jobs_excluded_manual = int(excluded_result.scalar_one())

        job_ids = [job.id for job in scorable_jobs]
        skills_by_job = await job_repository.skills_for_jobs(job_ids)

    counters.jobs_scored = len(scorable_jobs)
    jobs_by_id: dict[int, Job] = {job.id: job for job in scorable_jobs}

    for job in scorable_jobs:
        if job.work_mode == "remote":
            counters.jobs_remote += 1
        elif job.work_mode == "hybrid":
            counters.jobs_hybrid += 1

    run_id: int | None = None
    if not dry_run and counters.jobs_scored > 0:
        async with session_scope() as session:
            run = await ScoringRunRepository(session).start(weights_version, started_at)
            run_id = run.id

    if counters.jobs_scored > 0:
        async with session_scope() as session:
            target_ids = await select_target_user_ids(session, user_id)

        # Scales to the POOL, not to jobs_scored. pgvector's HNSW keeps
        # only ef_search candidates in flight, so an ef_search below the
        # LIMIT returns fewer rows than were asked for -- with no error.
        # At 99 rows the planner picks a sequential scan and this never
        # bites; it would begin biting silently once the table outgrows
        # that, which is the worst possible time to find out.
        ef_search = max(nearest_pool_size, _MIN_EF_SEARCH)

        for uid in target_ids:
            counters.users_considered += 1

            async with session_scope() as session:
                profile = await ProfileRepository(session).get_by_user_id(uid)

                version = None
                preferences = None
                if profile is not None:
                    version = await CVRepository(session).active_version_with_embedding(uid)
                    preferences = await UserRepository(session).get_preferences(uid)

                if not is_scorable_user(
                    profile_present=profile is not None,
                    embedded_version_present=version is not None,
                ):
                    counters.users_skipped_no_cv += 1

                    # One extra query, on the skipped branch only: a run
                    # that skips nobody pays nothing for this. The
                    # cheaper option is the single aggregate query in
                    # scripts/scorable_targets_check.py, and it was
                    # rejected twice over -- it observes the PLAN
                    # (counts read around the loop) where this observes
                    # the WORK, and reusing it would make that script
                    # share code with the thing it cross-checks, which
                    # is precisely what its independence test forbids.
                    active = await CVRepository(session).active_version(uid)
                    reason = classify_skip_reason(
                        profile_present=profile is not None,
                        active_version_present=active is not None,
                        embedded_version_present=version is not None,
                    )
                    if reason == SKIP_NO_PROFILE:
                        counters.users_skipped_no_profile += 1
                    elif reason == SKIP_NO_ACTIVE_CV:
                        counters.users_skipped_no_active_cv += 1
                    else:
                        counters.users_skipped_cv_not_embedded += 1
                    continue

                counters.users_scored += 1

                target_roles = preferences.target_roles if preferences else []
                preferred_locations = preferences.preferred_locations if preferences else []
                remote_only = preferences.remote_only if preferences else False
                notification_threshold = (
                    preferences.notification_threshold
                    if preferences
                    else _DEFAULT_NOTIFICATION_THRESHOLD
                )

                vector = list(version.embedding)
                # limit is the pool size, not jobs_scored. nearest_to()
                # does not filter is_excluded, so asking it for
                # jobs_scored rows draws a smaller window than the set
                # it selects from: every excluded job inside that
                # window takes a slot and pushes one real job off the
                # far end, unranked and uncounted. Ask for everything
                # the query can return, then drop the excluded ids
                # below where the drop can be counted.
                nearest = await JobRepository(session).nearest_to(
                    vector, limit=nearest_pool_size, ef_search=ef_search
                )

                user_pairs: list[tuple[int, ScoredPair]] = []

                for job, distance in nearest:
                    # nearest_to scopes to active+embedded only, not to
                    # is_excluded -- see JobRepository.nearest_to. An
                    # excluded job surfacing here is skipped rather
                    # than scored; it was already counted once, at the
                    # run level, in jobs_excluded_manual above.
                    #
                    # Two conditions, two branches, two counters. They
                    # were one `if` with an `or`, and that is precisely
                    # why the limit bug was invisible: both causes
                    # produced the same nothing. The second branch
                    # should never fire.
                    if job.is_excluded:
                        counters.nearest_dropped_excluded += 1
                        continue
                    if job.id not in jobs_by_id:
                        counters.nearest_dropped_unscorable += 1
                        continue

                    # NOT JobMatch.similarity. _to_similarity() there
                    # clamps at 0 for a different consumer, and using
                    # its output here would mean a negative cosine
                    # arrives as 0.0 and gets counted as "below the low
                    # anchor" by semantic_clamp_flags -- a correct
                    # clamp count attached to the wrong raw value. One
                    # clamp, inside score_semantic itself, in one place.
                    raw_similarity = 1.0 - distance

                    semantic = score_semantic(raw_similarity)
                    skill = score_skill(skills_by_job.get(job.id, []), profile.skills)
                    experience = score_experience(
                        profile.total_experience_years,
                        job.min_experience_years,
                        job.max_experience_years,
                    )
                    location = score_location(
                        job.location, job.work_mode, preferred_locations, remote_only
                    )
                    title = score_title(job.title, target_roles)
                    quality = assess_quality(job.company, job.location)

                    scored = combine(
                        semantic=semantic,
                        skill=skill,
                        experience=experience,
                        location=location,
                        title=title,
                        semantic_raw=raw_similarity,
                        quality=quality,
                    )

                    counters.pairs_scored += 1
                    if semantic.value is None:
                        counters.abstain_semantic += 1
                    if skill.value is None:
                        counters.abstain_skill += 1
                    if experience.value is None:
                        counters.abstain_experience += 1
                    if location.value is None:
                        counters.abstain_location += 1
                    if title.value is None:
                        counters.abstain_title += 1

                    clamped_low, clamped_high = semantic_clamp_flags(raw_similarity)
                    if clamped_low:
                        counters.semantic_clamped_low += 1
                    if clamped_high:
                        counters.semantic_clamped_high += 1
                    semantic_raw_values.append(raw_similarity)

                    if quality.is_agency:
                        counters.quality_penalty_agency += 1
                    if quality.missing_city:
                        counters.quality_penalty_no_city += 1

                    all_final_scores.append(scored.final_score)

                    # Three gates, not one, each compared with >= so the
                    # boundary value qualifies. Day 6's `median < 500`
                    # stayed silent when the median was exactly 500 --
                    # the boundary is the case that fails while looking
                    # like it should pass, and these three are tested
                    # at exactly their floors for the same reason.
                    if is_notify_eligible(
                        final_score=scored.final_score,
                        semantic_raw=raw_similarity,
                        weight_covered=scored.weight_covered,
                        notification_threshold=notification_threshold,
                    ):
                        counters.notify_eligible += 1

                    user_pairs.append((job.id, scored))

                ranked = rank(user_pairs)

                if not dry_run:
                    recommendation_repository = RecommendationRepository(session)
                    for job_id, scored, position in ranked:
                        scored_job = jobs_by_id[job_id]
                        fingerprint = compute_inputs_fingerprint(
                            profile_updated_at=profile.updated_at,
                            profile_skills=profile.skills,
                            job_embedding_source_hash=scored_job.embedding_source_hash,
                            job_skills_source_hash=scored_job.skills_source_hash,
                            weights_version=weights_version,
                        )
                        await recommendation_repository.upsert(
                            user_id=uid,
                            job_id=job_id,
                            scoring_run_id=run_id,
                            semantic_score=scored.semantic.value,
                            skill_score=scored.skill.value,
                            experience_score=scored.experience.value,
                            location_score=scored.location.value,
                            title_score=scored.title.value,
                            semantic_raw=scored.semantic_raw,
                            weight_covered=scored.weight_covered,
                            quality_multiplier=scored.quality.multiplier,
                            weights_version=weights_version,
                            inputs_fingerprint=fingerprint,
                            final_score=scored.final_score,
                            rank=position,
                            match_reasons=scored.match_reasons,
                        )

    # If either assertion fires, suspect the model before the data.
    # Day 7's equivalent funnel check fired twice and both times the
    # data was fine -- the model had no name for rows an aborted run
    # left behind. That was worth more than a passing check.
    assert counters.users_considered == counters.users_skipped_no_cv + counters.users_scored, (
        f"scoring funnel does not balance: considered={counters.users_considered} "
        f"skipped_no_cv={counters.users_skipped_no_cv} scored={counters.users_scored}"
    )
    assert counters.jobs_considered == (
        counters.jobs_skipped_no_embedding + counters.jobs_excluded_manual + counters.jobs_scored
    ), (
        f"scoring funnel does not balance: considered={counters.jobs_considered} "
        f"skipped_no_embedding={counters.jobs_skipped_no_embedding} "
        f"excluded_manual={counters.jobs_excluded_manual} scored={counters.jobs_scored}"
    )
    # The two assertions above are computed entirely from repository
    # counts taken BEFORE the scoring loop. They describe what the run
    # intended to do. Both of them balanced perfectly while the limit
    # bug was dropping real jobs, because neither one looks at what
    # nearest_to() actually returned.
    #
    # This third one does. Every scored user must produce exactly one
    # pair per scorable job -- no more, and critically no fewer. It is
    # the only check here that can fail because of something the loop
    # did rather than something the plan said.
    assert counters.pairs_scored == counters.jobs_scored * counters.users_scored, (
        f"scoring funnel does not balance: pairs_scored={counters.pairs_scored} "
        f"jobs_scored={counters.jobs_scored} users_scored={counters.users_scored} "
        f"dropped_excluded={counters.nearest_dropped_excluded} "
        f"dropped_unscorable={counters.nearest_dropped_unscorable}"
    )
    # A fourth, and it observes the work in a different way again from
    # the third. The third compares what the loop produced against a
    # product of two counts; this one compares four counters that all
    # increment INSIDE the loop, on the same branch, on the same pass.
    # It therefore cannot balance while the loop misattributes a cause:
    # there is no arithmetic path to a wrong breakdown that still sums.
    # What it catches is a fourth skip cause added later whose counter
    # somebody forgot to increment -- which would otherwise show up as
    # a breakdown quietly totalling less than the number above it.
    assert counters.users_skipped_no_cv == (
        counters.users_skipped_no_profile
        + counters.users_skipped_no_active_cv
        + counters.users_skipped_cv_not_embedded
    ), (
        f"skip breakdown does not sum: skipped_no_cv={counters.users_skipped_no_cv} "
        f"no_profile={counters.users_skipped_no_profile} "
        f"no_active_cv={counters.users_skipped_no_active_cv} "
        f"cv_not_embedded={counters.users_skipped_cv_not_embedded}"
    )

    distinct_score_count = len(set(all_final_scores))

    semantic_raw_min = min(semantic_raw_values) if semantic_raw_values else None
    semantic_raw_max = max(semantic_raw_values) if semantic_raw_values else None
    semantic_raw_median = (
        statistics.median(semantic_raw_values) if semantic_raw_values else None
    )

    score_min = min(all_final_scores) if all_final_scores else None
    score_max = max(all_final_scores) if all_final_scores else None
    score_median = statistics.median(all_final_scores) if all_final_scores else None

    all_signals_abstained_everywhere = counters.pairs_scored > 0 and (
        counters.abstain_semantic == counters.pairs_scored
        and counters.abstain_skill == counters.pairs_scored
        and counters.abstain_experience == counters.pairs_scored
        and counters.abstain_location == counters.pairs_scored
        and counters.abstain_title == counters.pairs_scored
    )

    status = select_status(
        jobs_scored=counters.jobs_scored,
        users_scored=counters.users_scored,
        pairs_scored=counters.pairs_scored,
        distinct_score_count=distinct_score_count,
        notify_eligible=counters.notify_eligible,
        all_signals_abstained_everywhere=all_signals_abstained_everywhere,
    )

    if not dry_run and run_id is not None:
        async with session_scope() as session:
            await ScoringRunRepository(session).finish(
                run_id=run_id,
                status=status.value,
                finished_at=datetime.now(timezone.utc),
                counters={
                    "users_considered": counters.users_considered,
                    "users_skipped_no_cv": counters.users_skipped_no_cv,
                    # The three-way breakdown is deliberately NOT written
                    # here. ScoringRunRepository.finish() compiles this dict
                    # straight into update(ScoringRun).values(**counters), so
                    # a key with no column on scoring_runs raises
                    # CompileError: Unconsumed column names -- which is what
                    # it did on the first non-dry run after the breakdown was
                    # added, because the suite has no database and every live
                    # check until then had been --dry-run.
                    #
                    # The breakdown IS persisted, in agent_runs, which has all
                    # three columns and a drift test holding them in step with
                    # build_run_summary(). Adding them to scoring_runs as well
                    # would be a second copy of the same numbers in a table
                    # whose funnel does not assert them. See the Day 10 Part 3
                    # record; adding the columns there instead remains open.
                    "users_scored": counters.users_scored,
                    "jobs_considered": counters.jobs_considered,
                    "jobs_skipped_no_embedding": counters.jobs_skipped_no_embedding,
                    "jobs_excluded_manual": counters.jobs_excluded_manual,
                    "jobs_scored": counters.jobs_scored,
                    "pairs_scored": counters.pairs_scored,
                    "abstain_semantic": counters.abstain_semantic,
                    "abstain_skill": counters.abstain_skill,
                    "abstain_experience": counters.abstain_experience,
                    "abstain_location": counters.abstain_location,
                    "abstain_title": counters.abstain_title,
                    "semantic_clamped_low": counters.semantic_clamped_low,
                    "semantic_clamped_high": counters.semantic_clamped_high,
                    "semantic_raw_min": semantic_raw_min,
                    "semantic_raw_max": semantic_raw_max,
                    "semantic_raw_median": semantic_raw_median,
                    "quality_penalty_agency": counters.quality_penalty_agency,
                    "quality_penalty_no_city": counters.quality_penalty_no_city,
                    "jobs_remote": counters.jobs_remote,
                    "jobs_hybrid": counters.jobs_hybrid,
                    "score_min": score_min,
                    "score_max": score_max,
                    "score_median": score_median,
                    "distinct_score_count": distinct_score_count,
                    "notify_eligible": counters.notify_eligible,
                },
            )

    return {
        "status": "dry_run" if dry_run else status.value,
        "run_id": run_id,
        "users_considered": counters.users_considered,
        "users_skipped_no_cv": counters.users_skipped_no_cv,
        "users_skipped_no_profile": counters.users_skipped_no_profile,
        "users_skipped_no_active_cv": counters.users_skipped_no_active_cv,
        "users_skipped_cv_not_embedded": counters.users_skipped_cv_not_embedded,
        "users_scored": counters.users_scored,
        "jobs_considered": counters.jobs_considered,
        "jobs_skipped_no_embedding": counters.jobs_skipped_no_embedding,
        "jobs_excluded_manual": counters.jobs_excluded_manual,
        "jobs_scored": counters.jobs_scored,
        "pairs_scored": counters.pairs_scored,
        "nearest_dropped_excluded": counters.nearest_dropped_excluded,
        "nearest_dropped_unscorable": counters.nearest_dropped_unscorable,
        "abstain_semantic": counters.abstain_semantic,
        "abstain_skill": counters.abstain_skill,
        "abstain_experience": counters.abstain_experience,
        "abstain_location": counters.abstain_location,
        "abstain_title": counters.abstain_title,
        "semantic_clamped_low": counters.semantic_clamped_low,
        "semantic_clamped_high": counters.semantic_clamped_high,
        "semantic_raw_min": semantic_raw_min,
        "semantic_raw_max": semantic_raw_max,
        "semantic_raw_median": semantic_raw_median,
        "quality_penalty_agency": counters.quality_penalty_agency,
        "quality_penalty_no_city": counters.quality_penalty_no_city,
        "jobs_remote": counters.jobs_remote,
        "jobs_hybrid": counters.jobs_hybrid,
        "score_min": score_min,
        "score_max": score_max,
        "score_median": score_median,
        "distinct_score_count": distinct_score_count,
        "notify_eligible": counters.notify_eligible,
    }
