"""Telegram adapters for the onboarding flow.

Every handler here does the same four things and nothing else:

    1. pull the primitives out of the Update
    2. open a session
    3. call OnboardingService
    4. send the BotReply back

No SQL, no branching on user state, no business rules. If a handler in
this file ever grows an `if`, that decision belongs in the service —
with one narrow exception. document_message checks
DocumentOutcome.stored to decide whether to schedule background CV
extraction. That is not a business decision made in the handler: the
service already decided whether the upload counted (`stored`), and the
handler is only acting on a flag it was handed, the same way it acts
on `reply.text`. What the handler could not do without Telegram is
schedule the task itself, since services stay Telegram-free.
"""

from __future__ import annotations

import logging

from telegram import Bot, Update
from telegram.ext import ContextTypes

from app.bot.rendering import tapped_button_label, to_markup
from app.db.models.cv import ExtractionStatus
from app.db.models.recommendation import (
    TRIGGER_SOURCE_ONBOARDING,
    TRIGGER_SOURCE_PREFERENCES,
)
from app.db.models.user import OnboardingState
from app.db.session import session_scope
from app.services.cv_embedding import embed_cv_version
from app.services.cv_extraction import extract_cv
from app.services.message_routing import route_text
from app.services.notification_delivery import score_and_notify_user
from app.services.onboarding import OnboardingService, get_onboarding_state

logger = logging.getLogger(__name__)


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return

    telegram_user = update.effective_user

    async with session_scope() as session:
        reply = await OnboardingService(session).start(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            full_name=telegram_user.full_name,
        )

    await update.message.reply_text(
        reply.text,
        reply_markup=to_markup(reply),
    )


async def restart_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return

    async with session_scope() as session:
        reply = await OnboardingService(session).restart(update.effective_user.id)

    await update.message.reply_text(reply.text, reply_markup=to_markup(reply))


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return

    async with session_scope() as session:
        reply = await OnboardingService(session).status(update.effective_user.id)

    await update.message.reply_text(reply.text)


async def document_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle an uploaded file."""
    if update.message is None or update.effective_user is None:
        return

    document = update.message.document
    if document is None:
        return

    async def download() -> bytes:
        """Fetch the bytes, only if the service asks for them.

        get_file is a network round trip and download is another. Both
        are skipped entirely when the name or size check already
        rejected the file.
        """
        telegram_file = await context.bot.get_file(document.file_id)
        return bytes(await telegram_file.download_as_bytearray())

    async with session_scope() as session:
        outcome = await OnboardingService(session).handle_document(
            telegram_id=update.effective_user.id,
            file_name=document.file_name,
            size_bytes=document.file_size,
            telegram_file_id=document.file_id,
            download=download,
        )

    await update.message.reply_text(
        outcome.reply.text, reply_markup=to_markup(outcome.reply)
    )

    if outcome.stored and outcome.user_id is not None:
        # Fired after the reply above is sent, not awaited: Gemini can
        # take several seconds, and accepting the file should not make
        # the user wait on it. context.application.create_task (rather
        # than bare asyncio.create_task) ties the task's lifetime to
        # the bot and routes an unexpected exception through the same
        # error_handler a normal update would hit, via `update=update`.
        context.application.create_task(
            _extract_and_notify(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                user_id=outcome.user_id,
            ),
            update=update,
        )


async def _extract_and_notify(bot: Bot, chat_id: int, user_id: int) -> None:
    """Run extraction and tell the user only if they need to do something.

    Opens no session of its own. extract_cv owns its transactions now,
    because it needs two with a network call in between — see the
    module docstring in app.services.cv_extraction.
    """
    result = await extract_cv(user_id)

    if result.status is ExtractionStatus.NO_TEXT_LAYER:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "📄 I couldn't find any readable text in that CV — it "
                "looks like a scan. Try exporting a text PDF instead; "
                "I can't read scanned images yet."
            ),
        )
    elif result.status is ExtractionStatus.FAILED:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Something went wrong while I was reading your CV. "
                "Your file is still saved — send it again, or try "
                "/restart, and I'll have another go."
            ),
        )
    elif result.status is ExtractionStatus.COMPLETE:
        await bot.send_message(
            chat_id=chat_id,
            text="🧠 I've read your CV and picked out your skills and experience.",
        )
        await _try_instant_recommendation(user_id, result.version_id)


async def _try_instant_recommendation(user_id: int, version_id: int) -> None:
    """Best-effort: embed unconditionally, then score and notify right
    away ONLY IF onboarding has actually reached COMPLETE.

    embed_cv_version always runs regardless of onboarding progress -- a
    CV should never miss the nightly embed_cvs backstop just because
    the user hasn't answered every preference question yet (see
    embed_cv_version's own docstring). Scoring is conditional: a user
    still mid-onboarding has no target_roles, no preferred_locations,
    and the 0.7 default threshold (job_scoring.py's own fallback for a
    user with no preference row) -- scoring them now would compute a
    match against data that isn't real yet, and nothing would ever redo
    it, because OnboardingService._save_threshold's own completion
    trigger is written to assume THIS path already covered the
    "extraction finished first" ordering.

    This is the Day 16 fix for a real defect in Day 15's onboarding
    instant path: Day 15's own live verification called
    _try_instant_recommendation directly with preferences already
    filled in (copying a real, complete profile onto a synthetic test
    user), so it never exercised a user racing ahead of their own
    extraction -- see CLAUDE.md's Day 16 entry for why this survived.

    Only the embed step needs its own try/except here. score_and_notify_
    user (app.services.notification_delivery) never raises -- see its
    own docstring -- so wrapping it a second time would be redundant.
    """
    try:
        outcome = await embed_cv_version(version_id)
    except Exception as exc:  # noqa: BLE001 - best-effort; the nightly pass is the real path
        # NEVER logger.exception() here -- same reasoning as
        # score_and_notify_user's except block in
        # app.services.notification_delivery: exc_info attaches the
        # full traceback, which prints str(exc) verbatim. Incident 13
        # was exactly a database connection failure whose message
        # carried a host, username and plaintext password. Only the
        # exception's class name is safe to log unconditionally.
        logger.error(
            "Instant onboarding CV embed failed for user_id=%s version_id=%s "
            "exception_type=%s",
            user_id,
            version_id,
            type(exc).__name__,
        )
        return

    if not outcome.embedded:
        return

    async with session_scope() as session:
        state = await get_onboarding_state(session, user_id)

    if state is not OnboardingState.COMPLETE:
        # Still mid-onboarding (or the user vanished/restarted
        # underneath this background task). OnboardingService.
        # _save_threshold's own completion trigger runs this instead,
        # once the user actually finishes answering every question.
        return

    await score_and_notify_user(
        user_id, trigger_source=TRIGGER_SOURCE_ONBOARDING, rescore=True
    )


async def photo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Reject photos of CVs explicitly.

    Sending a phone snap of a printed CV is a common and reasonable
    thing to try. Telegram delivers it as a photo, not a document, so it
    would otherwise fall through to the text handler and get a confusing
    answer. Day 4 has no OCR, so this is a real limit, and saying so is
    better than failing quietly.
    """
    if update.message is None:
        return

    await update.message.reply_text(
        "I can't read photos of a CV — I need the actual file. "
        "Send it as a PDF or DOCX document."
    )


async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle any non-command text, routed by the user's current step."""
    if update.message is None or update.effective_user is None:
        return

    if not update.message.text:
        return

    async with session_scope() as session:
        # route_text, not OnboardingService directly: a plain text
        # message could be answering onboarding OR a /preferences edit
        # in progress, and only the router knows which -- see its
        # docstring for the tie-break. Keeping that decision out of
        # this handler is what keeps it at "pull primitives, open a
        # session, call a service, send the reply" like every other
        # handler in this package.
        outcome = await route_text(
            session,
            update.effective_user.id,
            update.message.text,
        )

    await update.message.reply_text(
        outcome.reply.text,
        reply_markup=to_markup(outcome.reply),
    )

    if outcome.recommendation_trigger is not None and outcome.user_id is not None:
        # Only reachable via a saved target_roles/preferred_locations
        # answer -- see TextRouteOutcome and PreferencesEditOutcome's
        # docstrings. Scheduled the same way document_message schedules
        # _extract_and_notify: after the reply above, not awaited.
        context.application.create_task(
            score_and_notify_user(
                outcome.user_id,
                trigger_source=TRIGGER_SOURCE_PREFERENCES,
                rescore=outcome.recommendation_trigger == "rescore",
            ),
            update=update,
        )


async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle an inline-button tap."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    # Telegram shows a loading spinner on the button until this is
    # answered. Doing it before the database work means the button
    # stops spinning immediately rather than after the round trip.
    await query.answer()

    async with session_scope() as session:
        outcome = await OnboardingService(session).handle_callback(
            telegram_id=update.effective_user.id,
            data=query.data or "",
        )

    if query.message is not None:
        try:
            if outcome.answered:
                # A genuine answer: fold the choice into the question it
                # answered and drop the keyboard in the same edit, so
                # scrolling back shows one bubble carrying both rather
                # than a bare question with its buttons missing.
                label = tapped_button_label(query)
                text = query.message.text or ""
                if label is not None:
                    text = f"{text}\n\n✅ {label}"
                await query.edit_message_text(text=text, reply_markup=None)
            else:
                # Stale or unrecognised tap: nothing was saved, so there
                # is no choice to echo. Still strip the keyboard, so an
                # old question cannot be answered twice.
                await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 - message may be too old to edit
            logger.debug("Could not update answered message", exc_info=True)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=outcome.reply.text,
        reply_markup=to_markup(outcome.reply),
    )

    if outcome.recommendation_trigger is not None and outcome.user_id is not None:
        # Only reachable via OnboardingService._save_threshold's success
        # branch -- the single transition into OnboardingState.COMPLETE.
        # Scheduled after the reply above, not awaited, same as every
        # other background task in this project. If the CV isn't
        # embedded yet, run_scoring skips this user (cv_not_embedded)
        # and nothing is sent -- _try_instant_recommendation covers
        # that ordering instead, once embedding finishes.
        context.application.create_task(
            score_and_notify_user(
                outcome.user_id,
                trigger_source=TRIGGER_SOURCE_ONBOARDING,
                rescore=True,
            ),
            update=update,
        )
