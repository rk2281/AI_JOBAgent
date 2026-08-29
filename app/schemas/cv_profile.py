"""The shape of a CV once an LLM has read it.

This is handed to Gemini as a response schema — the model is
constrained to return JSON matching this shape, not just asked
nicely to. Validating happens as a side effect of parsing: if the
provider ever returns something that does not fit, constructing
CVProfile raises, and the caller treats that as an extraction
failure rather than trusting malformed data into the database.

Field shapes mirror app.db.models.profile.Profile, which is where
this ends up. They are declared independently rather than shared,
because they answer different questions: Profile describes a column
in Postgres; CVProfile describes what we are willing to accept from
a language model. Divergence between the two is a deliberate seam,
not duplication to clean up — allowing, for example, this schema to
tighten independently of a column type staying JSONB for flexibility.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExperienceEntry(BaseModel):
    """One job in a candidate's work history."""

    title: str
    company: str | None = None

    # Free text, not date, deliberately. CVs write dates as "Jan 2022",
    # "2022", "06/2021", "Present" — forcing ISO parsing at extraction
    # time means either Gemini fabricates a plausible-looking date for
    # something like "recently" or fails to answer at all. Keeping the
    # original text preserves what the CV actually said; turning it
    # into a real date, if ever needed, is a separate, optional step
    # that can fail without invalidating the whole extraction.
    start_date: str | None = None
    end_date: str | None = None

    # total_experience_years is a real float feeding a weighted match,
    # and free text cannot be arithmetic. These structured fields exist
    # so the calculation has numbers to work with while start_date /
    # end_date keep the original wording for display and audit.
    # A missing or unparseable date costs the calculation for this one
    # entry, not the whole extraction.
    #
    # Months, not just years, because this project's likely user has
    # under two years of experience — three roles totalling eighteen
    # months would round to 1 or 2 at year granularity, either of which
    # is materially wrong at 20% of a score.
    start_year: int | None = None
    start_month: int | None = None
    end_year: int | None = None
    end_month: int | None = None

    # Not redundant with end_year is None: that condition covers both
    # "still working here" and "the model could not read the date".
    # Only an explicit is_current justifies counting the role up to
    # today.
    is_current: bool = False
    description: str | None = None


class EducationEntry(BaseModel):
    """One qualification in a candidate's education history."""

    degree: str
    institution: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class CVProfile(BaseModel):
    """The structured profile extracted from one CV's raw text."""

    summary: str | None = None
    total_experience_years: float | None = None
    current_title: str | None = None
    location: str | None = None

    skills: list[str] = Field(default_factory=list)

    # Roles the CV suggests the candidate is aiming at, inferred from a
    # stated objective, a headline title, or the direction of recent
    # work — not necessarily a role they have held.
    #
    # Precedence is non-negotiable: whatever the user typed into
    # user_preferences.target_roles during onboarding always wins. A CV
    # says what someone has done; only the user says what they want
    # next. This field is deliberately not written to user_preferences
    # and not mirrored onto profiles — profiles is what the matching
    # feature reads, so a second competing set of target roles there
    # would make the precedence rule a matter of remembering which
    # column to read. Leaving it only in cv_versions.extracted_profile
    # lets the schema enforce the rule instead of discipline.
    target_roles: list[str] = Field(default_factory=list)

    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
