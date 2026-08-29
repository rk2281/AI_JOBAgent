"""Working out how much experience a candidate actually has.

The number this produces lands in profiles.total_experience_years and
feeds the experience match at 20% weight. It is computed here, in
Python, from the structured year/month fields on ExperienceEntry —
never asked of the language model.

That is a deliberate choice. A model asked "how many years is this?"
will answer confidently and unreproducibly: the same CV can yield 2.5
one run and 3 the next, and nothing in the system can say why. A
calculation can be re-run, unit-tested, and explained to a user who
asks "why did it say three years?" — which someone eventually will.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.cv_profile import ExperienceEntry

# Both bounds are inclusive month indices, so a role running Jan 2024
# to Jan 2024 counts as one month rather than zero.
_MONTHS_PER_YEAR = 12


def _month_index(year: int, month: int) -> int:
    """Collapse a year and month into a single sortable integer.

    Turning dates into plain integers is what makes the interval merge
    below simple. Comparing (2024, 12) with (2025, 1) as tuples works
    but arithmetic on them does not; 2024 * 12 + 12 and 2025 * 12 + 1
    subtract correctly.
    """
    return year * _MONTHS_PER_YEAR + month


def _entry_span(
    entry: ExperienceEntry, today: datetime
) -> tuple[int, int] | None:
    """Convert one role into an inclusive (start, end) month range.

    Returns None when the entry cannot be placed in time at all, which
    is a normal outcome rather than an error — a CV listing a role with
    no dates costs us that role's contribution, not the whole figure.

    Missing months are filled from the natural reading of a year-only
    range: a CV saying "2022 - 2023" means the whole of both years, so
    an absent start month becomes January and an absent end month
    becomes December. This is an assumption, and it is the one a human
    reader would make.
    """
    if entry.start_year is None:
        return None

    start = _month_index(entry.start_year, entry.start_month or 1)

    if entry.is_current:
        # Only an explicit is_current justifies counting up to today.
        # A missing end_year on its own is ambiguous — it covers both
        # "still there" and "the model could not read the date" — and
        # guessing "still there" would silently inflate the total.
        end = _month_index(today.year, today.month)
    elif entry.end_year is not None:
        end = _month_index(entry.end_year, entry.end_month or _MONTHS_PER_YEAR)
    else:
        return None

    if end < start:
        # A reversed range means the model misread something. Dropping
        # the entry is better than contributing a negative duration
        # that quietly cancels out real experience elsewhere.
        return None

    return start, end


def compute_total_experience_years(
    entries: list[ExperienceEntry],
    *,
    today: datetime | None = None,
) -> float | None:
    """Total professional experience in years, or None when unknowable.

    Overlapping roles are merged into a union of covered periods, not
    summed. Someone holding two jobs at once for a year has one year of
    experience, not two; summing would let a CV with concurrent roles
    or a consultancy listing several clients report a total that no
    calendar could produce. The union is also the version that can be
    defended out loud — "you were working from March 2022 to
    August 2024, which is thirty months" is checkable against the CV,
    while a sum is not.

    Returns None, not 0.0, when no entry carries usable dates. The two
    mean different things downstream: None is "we do not know", 0.0 is
    "we know, and it is zero" — a genuine fresher. Collapsing them
    would make every unparseable CV look like a candidate with no
    experience at all.

    `today` is injectable so the is_current case is testable; a test
    that depends on the real clock changes its expected answer every
    month.
    """
    reference = today or datetime.now(UTC)

    spans = [span for entry in entries if (span := _entry_span(entry, reference))]

    if not spans:
        return None

    # Merge overlapping and adjacent ranges. Sorting by start means a
    # single pass suffices: any range that overlaps an earlier one must
    # overlap the most recent range in the merged list.
    spans.sort()
    merged: list[list[int]] = [list(spans[0])]

    for start, end in spans[1:]:
        last = merged[-1]
        if start <= last[1] + 1:
            # +1 joins genuinely adjacent ranges — a role ending
            # December 2023 and the next starting January 2024 is
            # continuous employment, and treating it as two blocks
            # would be arithmetically identical here but would report
            # a misleading gap to anything that later reads the merge.
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])

    total_months = sum(end - start + 1 for start, end in merged)

    # One decimal place. The underlying data is month-precision at
    # best and often year-precision, so more digits would advertise an
    # accuracy that is not there.
    return round(total_months / _MONTHS_PER_YEAR, 1)
