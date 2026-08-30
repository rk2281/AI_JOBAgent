"""Turning a stored CV file into a structured profile.

extract_cv is the only function in this module anything outside it
should call. It takes a user_id and returns without raising for any
*expected* failure mode — an unreadable file, a scan with no text
layer, a Gemini error. The CV's own extraction_status column is where
the outcome lives; a caller does not have to catch an exception
correctly to find out what happened.

It knows nothing about Telegram. That is what gives it two callers
with nothing else in common: the background task fired from
app.bot.handlers.onboarding after a CV upload, and
scripts/extract_cv.py for running it by hand. A future FastAPI route
under app/api/routes/ would be a third caller with the same shape —
none of this file would need to change for that to work.

Why this function opens its own transactions instead of taking a
session. Until Fix 4 it accepted one, and held it across the Gemini
call. That is what made an ordinary slow response fatal: Neon closed
the connection with IdleInTransactionSessionTimeout while the
transaction sat open waiting on the network, and the write that
followed found no connection left. A transaction is a claim on a
database backend, and holding one across a call to a third party
means lending that backend to someone else's latency.

So the work is split into three phases:

  1. A short transaction that claims the CV and commits.
  2. The file read and the Gemini call, with NO session open.
  3. A short transaction that writes the results.

That means two transactions, which a single passed-in session cannot
provide, so the parameter is gone and the boundaries live here.

Does that weaken the atomicity session_scope() was built for? It
removes it, and it should. session_scope() exists so one Telegram
update either fully happens or fully does not — a guarantee worth
having when every step is a local database write. Extraction is not
that shape. It contains a multi-second call to a system that can
fail, hang, or succeed-then-strand-us, and no transaction can make
that call atomic with the writes around it. What replaces atomicity
is the status column: every path out of this function leaves the CV
in a terminal state, and a crash between phases leaves a stale
'extracting' claim that the staleness window in
CVRepository.claim_for_extraction reclaims.

This also supersedes the older argument in Part 6 of the Day 4
document — that failures had to be caught here because the caller's
rollback would erase the FAILED write. That reasoning was correct for
a single shared transaction. It no longer applies: the FAILED write
now happens in its own committed transaction. The broad catching
survives, but for a different and simpler reason — a caller should
learn what happened from a returned status rather than by catching
the right exception type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.models.cv import ExtractionStatus
from app.db.repositories.cv import CVRepository
from app.db.repositories.profile import ProfileRepository
from app.db.repositories.skill import SkillRepository, normalize_skill_names
from app.db.session import session_scope
from app.integrations.gemini import GeminiClient, GeminiExtractionError
from app.schemas.cv_profile import CVProfile
from app.services.cv_text import extract_raw_text
from app.services.experience import compute_total_experience_years

logger = logging.getLogger(__name__)

# Far longer than any healthy extraction, because this window only
# needs to distinguish "still running" from "the process died". Set
# close to the Gemini timeout it would let a merely slow extraction be
# claimed twice — the exact race the claim exists to prevent.
DEFAULT_STALE_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class ExtractionResult:
    """What happened when extract_cv ran, for a caller that wants to react.

    A background task uses this to decide whether to message the
    user; scripts/extract_cv.py uses it to decide what to print. The
    database row is the durable record — this is just the same
    answer, handed back synchronously so a caller is not forced to
    re-query for something it was already told.
    """

    status: ExtractionStatus
    error: str | None = None


async def extract_cv(
    user_id: int,
    *,
    gemini_client: GeminiClient | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> ExtractionResult:
    """Read a user's latest CV, extract its profile, and store the result.

    Opens two short transactions with the network call between them;
    see the module docstring for why. Returns EXTRACTING when another
    task already holds the claim, which is a normal outcome rather
    than a failure — the other task will finish the work.

    gemini_client is accepted as a parameter rather than always
    constructed inside, so a test can pass a fake without patching
    module internals — the same reasoning as CVIntakeService accepting
    storage_dir instead of always reading settings.cv_storage_dir.
    """
    # --- Phase 1: claim the row, then get out of the database -------
    async with session_scope() as session:
        cv_repo = CVRepository(session)

        cv = await cv_repo.latest_for_user(user_id)
        if cv is None:
            raise ValueError(f"No CV on file for user_id={user_id}")

        # Read what phase 2 needs before the session closes. Attribute
        # access on a detached instance would raise, and passing the
        # instance itself across the boundary invites exactly that.
        cv_id = cv.id
        file_type = cv.file_type
        storage_path = cv.storage_path

        if not await cv_repo.claim_for_extraction(cv_id, stale_after=stale_after):
            logger.info(
                "cv_id=%s is already being extracted; this task stands down",
                cv_id,
            )
            return ExtractionResult(status=ExtractionStatus.EXTRACTING)

    # --- Phase 2: no session is open past this point ----------------
    try:
        data = Path(storage_path).read_bytes()
        raw_text = extract_raw_text(file_type, data)
    except Exception as error:  # noqa: BLE001 - any bad file becomes FAILED
        return await _finish_failed(cv_id, str(error))

    if not raw_text:
        return await _finish_no_text(cv_id)

    client = gemini_client or GeminiClient()

    try:
        profile_data = await client.extract_profile(raw_text)
    except GeminiExtractionError as error:
        return await _finish_failed(cv_id, str(error))

    # --- Phase 3: write everything in one transaction ---------------
    async with session_scope() as session:
        cv_repo = CVRepository(session)

        cv = await cv_repo.by_id(cv_id)
        if cv is None:
            # The CV was deleted while we were talking to Gemini. There
            # is nothing left to attach the result to, and inventing a
            # row would resurrect data the user asked to remove.
            logger.warning("cv_id=%s vanished during extraction", cv_id)
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                error="CV was deleted during extraction",
            )

        cv.raw_text = raw_text

        version = await cv_repo.create_version(
            cv_id=cv_id,
            extracted_profile=profile_data.model_dump(mode="json"),
            extraction_model=client.model,
        )

        # Two reasons to keep the version row but not touch the
        # profile. Both leave a user better off than the alternative.
        if _is_empty_extraction(profile_data):
            # A parse that succeeded and said nothing. Day 4 wrote
            # status=complete here and overwrote the profile with the
            # emptiness, which is how a good profile could vanish
            # while every log line said success. The version row still
            # gets written above, so the bad extraction is auditable —
            # only the profile is spared.
            cv.extraction_status = ExtractionStatus.EMPTY.value
            cv.extracted_at = datetime.now(UTC)
            logger.warning(
                "Extraction for cv_id=%s parsed but was empty; "
                "profile left unchanged",
                cv_id,
            )
            return ExtractionResult(
                status=ExtractionStatus.EMPTY,
                error="Extraction produced no skills, experience or summary",
            )

        latest_id = await cv_repo.latest_id_for_user(user_id)
        if latest_id is not None and latest_id != cv_id:
            # The user uploaded a newer CV while this one was with
            # Gemini. The claim in phase 1 protects one CV from two
            # extractions; it says nothing about two CVs racing for
            # one profile row. Without this check the winner is
            # whichever Gemini call returned last, which may well be
            # the older file.
            cv.extraction_status = ExtractionStatus.COMPLETE.value
            cv.extracted_at = datetime.now(UTC)
            logger.info(
                "cv_id=%s finished but user_id=%s has newer cv_id=%s; "
                "profile not updated",
                cv_id,
                user_id,
                latest_id,
            )
            return ExtractionResult(status=ExtractionStatus.SUPERSEDED)

        profile = await ProfileRepository(session).get_or_create(user_id)
        profile.summary = profile_data.summary

        # Computed here, not taken from profile_data. The model is asked
        # for the structured start/end years and months; the arithmetic on
        # them is ours. A number the model produced could not be explained
        # to a user asking "why three years?", nor reproduced on a re-run.
        profile.total_experience_years = compute_total_experience_years(
            profile_data.experience
        )
        profile.current_title = profile_data.current_title
        profile.location = profile_data.location
        # Normalized keys, not the model's spellings. This is the division
        # of labour between the two tables: profiles.skills is what Day 8
        # matches against, so it holds catalog keys; the original wording
        # survives untouched in cv_versions.extracted_profile for display
        # and audit. Storing spellings here would mean a job asking for
        # "nodejs" silently failing to match a CV that wrote "Node.js".
        profile.skills = normalize_skill_names(profile_data.skills)
        profile.experience = [
            entry.model_dump(mode="json") for entry in profile_data.experience
        ]
        profile.education = [
            entry.model_dump(mode="json") for entry in profile_data.education
        ]
        profile.active_cv_version_id = version.id

        skill_repo = SkillRepository(session)
        for skill_name in profile_data.skills:
            # The catalog gets the original spelling; Skill.name is the
            # human-readable form and get_or_create normalizes it itself.
            await skill_repo.get_or_create(skill_name)

        await cv_repo.mark_others_superseded(user_id=user_id, keep_cv_id=cv_id)

        cv.extraction_status = ExtractionStatus.COMPLETE.value
        cv.extracted_at = datetime.now(UTC)

    logger.info(
        "Extraction complete for cv_id=%s user_id=%s skills=%d",
        cv_id,
        user_id,
        len(profile_data.skills),
    )
    return ExtractionResult(status=ExtractionStatus.COMPLETE)


async def _finish_failed(cv_id: int, message: str) -> ExtractionResult:
    """Record a failed extraction in its own committed transaction.

    Its own transaction is the whole point. Under the Day 4 design this
    write shared the caller's session, so an exception on the way out
    would roll it back and the CV would silently revert to its previous
    status. Committing here means a FAILED CV stays FAILED even if
    everything after this call goes wrong.
    """
    async with session_scope() as session:
        cv = await CVRepository(session).by_id(cv_id)
        if cv is not None:
            # extraction_error is TEXT (unbounded), but an unbounded
            # provider error message logged into a database column
            # forever is a footgun waiting to happen, not a feature.
            cv.extraction_status = ExtractionStatus.FAILED.value
            cv.extraction_error = message[:2000]
            cv.extracted_at = datetime.now(UTC)

    logger.warning("Extraction failed for cv_id=%s: %s", cv_id, message)
    return ExtractionResult(status=ExtractionStatus.FAILED, error=message)


async def _finish_no_text(cv_id: int) -> ExtractionResult:
    """Record that the file held no readable text.

    Distinct from FAILED: nothing went wrong, there was simply nothing
    to read, and no retry will change that until OCR exists.
    """
    async with session_scope() as session:
        cv = await CVRepository(session).by_id(cv_id)
        if cv is not None:
            cv.extraction_status = ExtractionStatus.NO_TEXT_LAYER.value
            cv.extracted_at = datetime.now(UTC)

    logger.info("No text layer for cv_id=%s", cv_id)
    return ExtractionResult(status=ExtractionStatus.NO_TEXT_LAYER)


def _is_empty_extraction(profile_data: CVProfile) -> bool:
    """Whether a successfully-parsed extraction actually said anything.

    The check that Day 4 lacked. Gemini can return a well-formed
    response matching the schema in which every array is empty and
    every string is null — most often when the response schema was
    built without a `required` list, but also on a CV whose layout
    defeats it. Nothing raises. status=complete gets written. The
    profile is overwritten with nothing.

    target_roles is excluded on purpose. It is inferred rather than
    read off the page, so a response carrying only target_roles has
    guessed rather than extracted, and treating that as a usable
    profile would defeat the point of this function.
    """
    return not (
        (profile_data.summary or "").strip()
        or profile_data.skills
        or profile_data.experience
        or profile_data.education
    )
