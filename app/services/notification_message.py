"""What a job notification says, as a pure function of stored fields.

No database, no Telegram, no ORM. It takes primitives and returns a
BotReply -- the same framework-free shape every onboarding prompt
returns -- so the exact bytes a user will receive can be asserted in a
test with no bot token, no network and no rows.

IT INVENTS NOTHING

Every line is built from a column that exists, and a line whose column
is NULL is OMITTED rather than filled in. That is the same rule the
scoring model follows when a signal abstains: a missing value is
absent, not zero, and "Company: None" or "Experience: 0 years" would
be this layer stating something the data never said. 94 of 99 jobs have
no `work_mode` and most have no experience bounds, so this is the
common case and not an edge case.

NO parse_mode. DELIBERATE, AND NOT LAZINESS

Telegram's Markdown and HTML modes both require escaping, and the text
here is job titles and company names ingested from a third party -- the
one category of string guaranteed to contain the characters that break
a parser. A title holding `C++ / .NET*` breaks Markdown; one holding
`R&D <Senior>` breaks HTML. The failure is not cosmetic either: an
unescaped entity makes the API reject the whole message, so the user
receives NOTHING and the run records a delivery failure whose cause is
a punctuation mark in a job title.

Plain text has none of that, and costs only bold. Telegram auto-links a
bare URL in plain text, so the apply link still works.

MESSAGE LENGTH

Telegram's hard limit is 4096 characters. Nothing here approaches it --
the reasons are capped and the description is not included at all -- but
the cap is applied anyway, because "nothing here approaches it" is a
statement about today's data and the truncation is one line.
"""

from __future__ import annotations

from app.db.models.recommendation import FeedbackAction
from app.services.replies import BotReply, Button

# Callback data prefix for every feedback button. Three colon-separated
# parts, exactly like onboarding's "onb:step:value", because
# OnboardingService.handle_callback() rejects anything that is not
# three parts -- matching its shape keeps one parser convention rather
# than two.
#
# This prefix is also what the CallbackQueryHandler pattern matches on.
# Before Day 11 the onboarding handler had NO pattern and would have
# swallowed these.
CALLBACK_PREFIX = "fb"

# Longest a callback_data may be, from Telegram's Bot API. Not a style
# choice: the API rejects a longer one, and it would be rejected when a
# user TAPS the button rather than when the message is sent, which is
# the worst possible time to find out.
MAX_CALLBACK_DATA_BYTES = 64

# How many "why it matches" bullets to show. The list can run longer;
# a notification is a nudge to open the link, not a scoring report, and
# the full reasoning is in recommendations.match_reasons for anyone who
# wants it.
MAX_MATCH_REASONS = 4

MAX_MESSAGE_CHARS = 4096

# Score bands, purely presentational -- they change no decision and
# gate nothing. Bands rather than one fixed heading because the manual
# test script can send a recommendation of any score at all, and a
# message headed "Strong Job Match" above a 41% match would be this
# layer overstating what the model said.
_STRONG_MATCH = 0.85
_GOOD_MATCH = 0.70


def build_feedback_callback(action: FeedbackAction, job_id: int) -> str:
    """"fb:saved:1234". The inverse of parse_feedback_callback()."""
    return f"{CALLBACK_PREFIX}:{action.value}:{job_id}"


def format_experience(minimum: int | None, maximum: int | None) -> str | None:
    """"1-3 years", "3+ years", "up to 3 years", or None.

    None means the job did not say, and the caller omits the line. It
    is by far the most common answer: `jobs_with_experience_bounds` is
    0 today, because Adzuna truncates descriptions at 500 characters
    and most never mention years at all. A default of "0 years" here
    would turn a source-data ceiling into a confident claim about every
    job in the database.
    """
    if minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None:
        if minimum == maximum:
            return f"{minimum} years"
        return f"{minimum}-{maximum} years"
    if minimum is not None:
        return f"{minimum}+ years"
    return f"up to {maximum} years"


# Prefix combine() puts on a signal that had no data, and the sentinel
# it puts first when NONE of them did. See app/services/scoring.py.
_ABSTAIN_PREFIX = "abstained:"
_NO_SIGNAL_SENTINEL = "no signal had data"


def user_facing_reasons(match_reasons: list[str] | None) -> list[str]:
    """The stored reasons, minus the ones that are not reasons it matched.

    `recommendations.match_reasons` deliberately holds BOTH what matched
    and what abstained, and combine()'s docstring gives the reason:
    "an explanation that only names what matched is an explanation that
    hides why the score is as low as it is." That is correct for the
    stored row, which has to be readable on its own by somebody asking
    why a score came out where it did.

    A notification is a different consumer asking a different question.
    Under a heading that says "Why it matches", the line

        • abstained: job lists no extractable skills

    does not answer it -- it is a fact about our data coverage, phrased
    as though it were a property of the job. On real data this is the
    common case rather than a rarity: the skill signal abstains on 96 of
    98 pairs and the experience signal on all 98, so most notifications
    would carry more non-reasons than reasons.

    PRESENTATION ONLY. Nothing is dropped from the database, no scoring
    input changes, and `--top` in scripts/score_jobs.py still prints the
    full list. This filters what one consumer displays, which is the
    layer where "what does this mean to a person" is allowed to be
    answered differently from "what happened".

    When everything abstained this returns [] and the caller omits the
    section, which is right: there is nothing to say about why it
    matched, and saying so at length would be worse than saying nothing.
    """
    reasons = []
    for reason in match_reasons or []:
        stripped = reason.strip()
        if not stripped:
            continue
        if stripped.startswith(_ABSTAIN_PREFIX):
            continue
        if stripped == _NO_SIGNAL_SENTINEL:
            continue
        reasons.append(stripped)
    return reasons


def format_match_percent(final_score: float) -> str:
    """0.8734 -> "87%".

    Rounded for display only. The stored score keeps its full
    precision, and nothing downstream reads this string.
    """
    return f"{round(final_score * 100)}%"


def _heading(final_score: float) -> str:
    if final_score >= _STRONG_MATCH:
        return "\U0001f525 Strong Job Match"
    if final_score >= _GOOD_MATCH:
        return "✨ Good Job Match"
    return "\U0001f4cc Possible Job Match"


def format_job_notification(
    *,
    job_id: int,
    title: str,
    company: str | None,
    location: str | None,
    min_experience_years: int | None,
    max_experience_years: int | None,
    final_score: float,
    match_reasons: list[str] | None,
    url: str | None,
) -> BotReply:
    """One recommendation, as the message and buttons a user will see.

    Takes primitives rather than a Recommendation and a Job, so the
    formatter can be tested without constructing ORM instances and
    cannot accidentally trigger a lazy load on a detached row -- which
    is the failure mode app/workflows/state.py's "counters, not rows"
    rule exists to avoid one layer up.

    A missing `url` still produces a message. The job is real and the
    match is real; the user simply gets no link, which is worth strictly
    more than silence. `jobs.url` is NOT NULL so this should be
    unreachable, and it is handled anyway because "should be
    unreachable" is not the same as "is".
    """
    lines = [_heading(final_score), "", title.strip(), ""]

    if company:
        lines.append(f"\U0001f3e2 Company: {company.strip()}")
    if location:
        lines.append(f"\U0001f4cd Location: {location.strip()}")

    experience = format_experience(min_experience_years, max_experience_years)
    if experience:
        lines.append(f"\U0001f4bc Experience: {experience}")

    lines.extend(["", f"\U0001f3af Match: {format_match_percent(final_score)}"])

    reasons = user_facing_reasons(match_reasons)
    if reasons:
        lines.extend(["", "Why it matches:"])
        lines.extend(f"• {reason}" for reason in reasons[:MAX_MATCH_REASONS])

    if url:
        lines.extend(["", f"\U0001f517 Apply: {url.strip()}"])

    text = "\n".join(lines)[:MAX_MESSAGE_CHARS]

    # One row of three. Telegram lays a single row out horizontally and
    # these are short enough to stay readable; three separate rows would
    # push the apply link off a phone screen.
    buttons = [
        [
            Button(
                "\U0001f44d Interested",
                build_feedback_callback(FeedbackAction.INTERESTED, job_id),
            ),
            Button(
                "\U0001f516 Save",
                build_feedback_callback(FeedbackAction.SAVED, job_id),
            ),
            Button(
                "\U0001f6ab Not Relevant",
                build_feedback_callback(FeedbackAction.NOT_RELEVANT, job_id),
            ),
        ]
    ]

    return BotReply(text=text, buttons=buttons)
