"""Turning stored profile data into something a user can read.

Split into a pure function (render_profile) and a service that feeds
it (ProfileService, in app/services/profile.py). The split is what
makes the interesting part testable: every branch below is a decision
about what a user sees, and none of them need Postgres, a bot token,
or a Gemini key to check.

The display/matching split this file exists to honour: skills shown
here come from cv_versions.extracted_profile, which holds the model's
original spellings. profiles.skills holds normalized catalog keys and
is for matching only. Rendering 'cpp' and 'dotnet' at a user would be
technically accurate and read like a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models.cv import ExtractionStatus
from app.services.replies import BotReply

# Enough to show what the bot understood, short enough to read on a
# phone without scrolling past the part that matters.
MAX_SKILLS_SHOWN = 12
MAX_ROLES_SHOWN = 2


@dataclass(frozen=True)
class ProfileSnapshot:
    """Everything /profile needs, already loaded, with no ORM objects.

    Plain values rather than a Profile and a CVVersion because the
    renderer must be callable from a test with no database. It also
    keeps the renderer honest: it cannot lazily reach for a
    relationship it was not explicitly given, which is the usual way a
    display function quietly acquires a query.
    """

    has_user: bool = False
    has_cv: bool = False

    cv_file_name: str | None = None
    extraction_status: str | None = None
    extraction_error: str | None = None

    # The raw JSON from cv_versions.extracted_profile, or None when no
    # version has ever been written for this user.
    extracted_profile: dict[str, Any] | None = field(default=None)


def _is_empty_profile(data: dict[str, Any] | None) -> bool:
    """Whether an extracted profile carries anything worth showing.

    This is the display-side counterpart to the EMPTY status. A CV
    extracted before EMPTY existed can still be sitting at COMPLETE
    with nothing in it, so /profile cannot rely on the status alone to
    know whether it has something to render.
    """
    if not data:
        return True

    summary = (data.get("summary") or "").strip()

    return not (
        summary
        or data.get("skills")
        or data.get("experience")
        or data.get("education")
    )


def render_profile(snapshot: ProfileSnapshot) -> BotReply:
    """Describe what the bot currently knows about a candidate.

    Never triggers work and never reports progress it has not
    observed. Each CV state gets its own wording, because the
    difference between 'I haven't read it yet' and 'I read it and got
    nothing' is the difference between waiting and re-uploading.
    """
    if not snapshot.has_user:
        return BotReply(text="I don't know you yet — send /start to begin.")

    if not snapshot.has_cv:
        return BotReply(
            text=(
                "I don't have a CV for you yet.\n\n"
                "Send me one as a PDF or DOCX and I'll read it."
            )
        )

    status = snapshot.extraction_status
    file_name = snapshot.cv_file_name or "your CV"

    if status == ExtractionStatus.PENDING.value:
        return BotReply(
            text=(
                f"📄 I have {file_name} on file but haven't read it yet.\n\n"
                "Send it again and I'll process it."
            )
        )

    if status == ExtractionStatus.EXTRACTING.value:
        return BotReply(
            text=(
                f"⏳ I'm reading {file_name} right now. "
                "Give me about thirty seconds, then try /profile again."
            )
        )

    if status == ExtractionStatus.NO_TEXT_LAYER.value:
        return BotReply(
            text=(
                f"📄 {file_name} looks like a scan — there's no text in it "
                "I can read.\n\nExport a text PDF from Word or Google Docs "
                "and send that instead."
            )
        )

    if status == ExtractionStatus.FAILED.value:
        reason = snapshot.extraction_error or "an unexpected error"
        return BotReply(
            text=(
                f"⚠️ I couldn't read {file_name}.\n\n"
                f"Reason: {reason}\n\n"
                "Send it again, or send a different file, and I'll retry."
            )
        )

    if status == ExtractionStatus.EMPTY.value or _is_empty_profile(
        snapshot.extracted_profile
    ):
        # The case Day 4 got wrong. Reporting a successful read and
        # then showing an empty profile is worse than reporting
        # nothing, because the user has no reason to try again.
        return BotReply(
            text=(
                f"🤔 I read {file_name}, but couldn't pull anything useful "
                "out of it — no skills, no work history.\n\n"
                "That usually means the layout confused me. A simpler "
                "one-column CV normally works. Your previous profile, if "
                "you had one, is untouched."
            )
        )

    return BotReply(text="\n".join(_profile_lines(snapshot)))


def _profile_lines(snapshot: ProfileSnapshot) -> list[str]:
    """Build the body of a populated profile, one line per fact.

    Plain text, deliberately. Every value here can originate in a CV or
    a filename, and Telegram's legacy Markdown parser has no escape
    mechanism — see the comment in app.bot.handlers.profile.
    """
    data = snapshot.extracted_profile or {}
    lines: list[str] = ["Here's what I know about you", ""]

    if data.get("current_title"):
        lines.append(f"Role: {data['current_title']}")

    if data.get("location"):
        lines.append(f"Location: {data['location']}")

    experience = data.get("experience") or []
    years = data.get("total_experience_years")
    if years is not None:
        # Deliberately not recomputed here. profiles.total_experience_years
        # is the number matching uses, computed in
        # app/services/experience.py from a union of covered periods.
        # A display that did its own arithmetic could disagree with the
        # scorer and there would be no way to tell which was right.
        lines.append(f"Experience: {years:.1f} years")

    if data.get("summary"):
        lines.extend(["", data["summary"]])

    skills = data.get("skills") or []
    if skills:
        shown = skills[:MAX_SKILLS_SHOWN]
        remainder = len(skills) - len(shown)
        text = ", ".join(shown)
        if remainder > 0:
            text += f" (+{remainder} more)"
        lines.extend(["", f"Skills: {text}"])

    if experience:
        lines.extend(["", "Recent roles"])
        for entry in experience[:MAX_ROLES_SHOWN]:
            company = entry.get("company")
            where = f" at {company}" if company else ""
            when = _format_period(entry)
            lines.append(f"• {entry.get('title', 'Role')}{where}{when}")

    education = data.get("education") or []
    if education:
        first = education[0]
        institution = first.get("institution")
        where = f" — {institution}" if institution else ""
        lines.extend(["", f"Education: {first.get('degree', '')}{where}"])

    target_roles = data.get("target_roles") or []
    if target_roles:
        # Labelled as inferred on purpose. The user's own onboarding
        # answer in user_preferences.target_roles is what actually
        # drives matching; this is a secondary signal that is never
        # written there. Showing it unlabelled would imply it counts.
        lines.extend(
            ["", f"Roles your CV points at: {', '.join(target_roles)}"]
        )
        lines.append("(a guess from your CV — your stated preferences win)")

    lines.extend(
        ["", f"From {snapshot.cv_file_name or 'your CV'}. Send /update_cv to replace it."]
    )

    return lines


def _format_period(entry: dict[str, Any]) -> str:
    """Render an experience entry's dates, using whatever it has.

    Reads start_date/end_date, the free-text fields, rather than the
    structured year/month ones. The structured fields exist for
    arithmetic; these preserve what the CV literally said, which is
    what a user recognises.
    """
    start = entry.get("start_date")
    if not start:
        return ""

    end = "Present" if entry.get("is_current") else (entry.get("end_date") or "")
    return f" ({start} – {end})" if end else f" ({start})"
