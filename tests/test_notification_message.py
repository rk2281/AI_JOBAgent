"""The notification message, asserted on the exact bytes a user receives.

No database, no bot token, no network. format_job_notification() takes
primitives and returns a BotReply, which is what makes this possible --
and is why it takes primitives rather than a Recommendation and a Job.

The theme running through these: a NULL column must produce a MISSING
LINE, never a line reading "None" or "0". 94 of 99 jobs have no
work_mode and none has experience bounds, so absent data is the normal
case here rather than an edge case, and a formatter that invents a
value states something the scoring model was careful not to.
"""

from __future__ import annotations

from app.db.models.recommendation import FeedbackAction
from app.services.notification_message import (
    CALLBACK_PREFIX,
    MAX_CALLBACK_DATA_BYTES,
    MAX_MATCH_REASONS,
    MAX_MESSAGE_CHARS,
    build_feedback_callback,
    format_experience,
    format_job_notification,
    format_match_percent,
)


def _format(**overrides):
    kwargs = {
        "job_id": 42,
        "title": "Backend Python Developer",
        "company": "Example Company",
        "location": "Delhi",
        "min_experience_years": 1,
        "max_experience_years": 3,
        "final_score": 0.8734,
        "match_reasons": ["Python", "FastAPI", "PostgreSQL"],
        "url": "https://example.com/jobs/42",
    }
    kwargs.update(overrides)
    return format_job_notification(**kwargs)


# --- the whole message ---------------------------------------------------


def test_a_complete_recommendation_renders_every_field() -> None:
    reply = _format()

    assert "Backend Python Developer" in reply.text
    assert "Example Company" in reply.text
    assert "Delhi" in reply.text
    assert "1-3 years" in reply.text
    assert "87%" in reply.text
    assert "Python" in reply.text
    assert "https://example.com/jobs/42" in reply.text


def test_the_match_percent_is_rounded_not_truncated() -> None:
    """0.8734 -> 87%, and 0.876 -> 88%. Display only; nothing reads it back."""
    assert format_match_percent(0.8734) == "87%"
    assert format_match_percent(0.876) == "88%"
    assert format_match_percent(1.0) == "100%"
    assert format_match_percent(0.0) == "0%"


# --- absent is not zero --------------------------------------------------


def test_a_missing_company_omits_the_line_rather_than_printing_none() -> None:
    reply = _format(company=None)
    assert "Company" not in reply.text
    assert "None" not in reply.text


def test_a_missing_location_omits_the_line_rather_than_printing_none() -> None:
    reply = _format(location=None)
    assert "Location" not in reply.text
    assert "None" not in reply.text


def test_missing_experience_omits_the_line_rather_than_printing_zero() -> None:
    """The common case, not an edge case.

    `jobs_with_experience_bounds` is 0 -- Adzuna truncates descriptions
    at 500 characters and most never mention years. "Experience: 0
    years" would turn that source-data ceiling into a confident claim
    about every job in the database.
    """
    reply = _format(min_experience_years=None, max_experience_years=None)
    assert "Experience" not in reply.text
    assert "0 years" not in reply.text


def test_no_match_reasons_omits_the_whole_section() -> None:
    reply = _format(match_reasons=[])
    assert "Why it matches" not in reply.text

    reply = _format(match_reasons=None)
    assert "Why it matches" not in reply.text


def test_abstentions_are_not_listed_as_reasons_it_matched() -> None:
    """combine() stores both what matched and what abstained, on purpose.
    A heading saying "Why it matches" is not the place for the second
    kind -- and on real data the second kind is the majority: the skill
    signal abstains on 96 of 98 pairs, experience on all 98."""
    reply = _format(
        match_reasons=[
            "preferred location match",
            "abstained: job lists no extractable skills",
            "abstained: no experience bounds on the job",
        ]
    )

    assert "preferred location match" in reply.text
    assert "abstained" not in reply.text
    assert reply.text.count("•") == 1


def test_a_fully_abstained_row_omits_the_section_rather_than_explaining_itself() -> None:
    """weight_covered == 0.0 is a real state. There is genuinely nothing
    to say about why it matched, and saying so at length is worse than
    saying nothing."""
    reply = _format(
        match_reasons=[
            "no signal had data",
            "abstained: job lists no extractable skills",
        ]
    )

    assert "Why it matches" not in reply.text
    assert "no signal had data" not in reply.text


def test_filtering_reasons_does_not_touch_the_stored_list() -> None:
    """Presentation only. The database keeps every reason, and
    scripts/score_jobs.py --top still prints them all."""
    from app.services.notification_message import user_facing_reasons

    stored = ["location match", "abstained: no skills"]
    assert user_facing_reasons(stored) == ["location match"]
    assert stored == ["location match", "abstained: no skills"]


def test_blank_match_reasons_are_dropped_not_rendered_as_empty_bullets() -> None:
    reply = _format(match_reasons=["Python", "   ", ""])
    assert "• Python" in reply.text
    assert "• \n" not in reply.text
    assert reply.text.count("•") == 1


# --- experience formatting, at each boundary -----------------------------


def test_experience_ranges_read_the_way_a_person_would_say_them() -> None:
    assert format_experience(1, 3) == "1-3 years"
    assert format_experience(5, None) == "5+ years"
    assert format_experience(None, 3) == "up to 3 years"
    assert format_experience(None, None) is None


def test_an_equal_minimum_and_maximum_is_not_rendered_as_a_range() -> None:
    """"3-3 years" is what the naive branch produces, and it reads as a
    bug to anyone who sees it."""
    assert format_experience(3, 3) == "3 years"


def test_a_zero_minimum_is_not_confused_with_an_absent_one() -> None:
    """0 is falsy in Python and absent is None. A job explicitly open to
    freshers says something; a job that never mentioned experience does
    not, and `if minimum:` would merge the two."""
    assert format_experience(0, 2) == "0-2 years"
    assert format_experience(0, None) == "0+ years"


# --- the URL -------------------------------------------------------------


def test_a_missing_url_still_produces_a_message() -> None:
    """jobs.url is NOT NULL so this should be unreachable. "Should be
    unreachable" is not "is", and a real match with no link is worth
    more to a user than silence."""
    reply = _format(url=None)
    assert "Backend Python Developer" in reply.text
    assert "Apply" not in reply.text

    reply = _format(url="")
    assert "Apply" not in reply.text
    assert "Backend Python Developer" in reply.text


# --- the buttons ---------------------------------------------------------


def test_every_notification_carries_the_three_feedback_buttons() -> None:
    reply = _format()

    assert len(reply.buttons) == 1, "one row, so the apply link stays on screen"
    row = reply.buttons[0]
    assert len(row) == 3

    data = [button.callback_data for button in row]
    assert data == ["fb:interested:42", "fb:saved:42", "fb:not_relevant:42"]


def test_callback_data_carries_the_job_id_and_not_the_user_id() -> None:
    """A user id in a button is a user id somebody can edit. Telegram
    delivers the tapper's own account alongside the data for free, so
    carrying it would add an attack surface and no information."""
    reply = _format(job_id=999)
    for button in reply.buttons[0]:
        assert button.callback_data.endswith(":999")
        assert button.callback_data.startswith(f"{CALLBACK_PREFIX}:")


def test_callback_data_fits_telegrams_limit_even_for_an_implausible_job_id() -> None:
    """The API rejects callback_data over 64 bytes when the user TAPS,
    not when the message is sent -- the worst possible time to find out.

    Asserted against a job id far beyond anything this table will hold,
    because the check is worthless at job_id=42.
    """
    for action in FeedbackAction:
        data = build_feedback_callback(action, 9_999_999_999)
        assert len(data.encode("utf-8")) <= MAX_CALLBACK_DATA_BYTES


def test_the_callback_data_shape_matches_onboardings() -> None:
    """Three colon-separated parts, like "onb:remote:yes".
    OnboardingService.handle_callback rejects anything that is not three
    parts, and one convention across the project beats two."""
    data = build_feedback_callback(FeedbackAction.NOT_RELEVANT, 7)
    assert len(data.split(":")) == 3


# --- length and hostile input --------------------------------------------


def test_the_message_is_capped_below_telegrams_limit() -> None:
    reply = _format(
        title="Very Long Title " * 500,
        match_reasons=["reason " * 50] * 20,
    )
    assert len(reply.text) <= MAX_MESSAGE_CHARS


def test_only_the_first_few_match_reasons_are_shown() -> None:
    reply = _format(match_reasons=[f"skill{i}" for i in range(20)])
    assert reply.text.count("•") == MAX_MATCH_REASONS


def test_markdown_and_html_characters_survive_verbatim() -> None:
    """The reason no parse_mode is used.

    Job titles come from a third party and are the one category of
    string guaranteed to contain the characters that break a parser.
    Under Markdown or HTML an unescaped entity makes Telegram reject the
    WHOLE message, so the user receives nothing and the run records a
    delivery failure caused by a punctuation mark in a job title.

    Plain text has no such failure mode, and this asserts the text
    reaches the reply unmangled rather than being escaped by something.
    """
    hostile = "C++ / .NET* Developer <Senior> _R&D_ [urgent]"
    reply = _format(title=hostile, company="A & B <Ltd>")

    assert hostile in reply.text
    assert "A & B <Ltd>" in reply.text
    assert "\\" not in reply.text


def test_surrounding_whitespace_is_trimmed_from_ingested_fields() -> None:
    reply = _format(title="  Backend Developer  ", company="  Acme  ")
    assert "Backend Developer\n" in reply.text
    assert "Company: Acme" in reply.text


# --- the heading band ----------------------------------------------------


def test_the_heading_does_not_overstate_a_weak_match() -> None:
    """The manual test script can send a recommendation of any score, so
    a fixed "Strong Job Match" heading would let this layer claim
    something the model never said."""
    strong = _format(final_score=0.95).text.splitlines()[0]
    good = _format(final_score=0.75).text.splitlines()[0]
    weak = _format(final_score=0.41).text.splitlines()[0]

    assert "Strong" in strong
    assert "Good" in good
    assert "Strong" not in weak and "Good" not in weak


def test_the_heading_bands_are_inclusive_at_exactly_their_floors() -> None:
    """Every threshold in this project uses >= and is tested AT its
    floor, not near it. Day 6's `median < 500` stayed silent when the
    median was exactly 500."""
    assert "Strong" in _format(final_score=0.85).text.splitlines()[0]
    assert "Good" in _format(final_score=0.70).text.splitlines()[0]
