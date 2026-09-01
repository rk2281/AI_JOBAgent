"""Fold location strings written differently onto one key.

Needed in two places that must agree, which is why it is a service
rather than a helper inside either of them:

  ingestion  -- so the same job posted as "Gurgaon" and "Gurugram"
                produces the same content_hash and is recognised as
                one job rather than two
  matching   -- so a user who typed "Gurgaon" during onboarding
                matches a posting that says "Gurugram" (Day 8)

If those two used different rules the failure would be silent and
one-sided: dedup would work, matching would quietly score zero, and
nothing would look broken.

Deliberately small. This is a spelling and suffix problem, not a
geography problem -- it does not know that Noida is near Delhi and it
should not learn. Proximity is a different question with a different
answer (a geocoder), and pretending a lookup table can answer it is
how a small correct thing turns into a large wrong one.

Same shape and same reasoning as PUNCTUATION_ALIASES in
app/db/repositories/skill.py.
"""

from __future__ import annotations

# Confirmed relevant by measurement, not assumed: Adzuna's `where` is
# a literal match with no regional umbrella -- "Delhi" returned fewer
# results than "Noida" for the same query and window, so the NCR
# cities really are separate strings that no provider will reconcile.
LOCATION_ALIASES: dict[str, str] = {
    "gurugram": "gurgaon",
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "calcutta": "kolkata",
    "madras": "chennai",
    "new delhi": "delhi",
    "noida sector 62": "noida",
    "greater noida": "noida",
    "trivandrum": "thiruvananthapuram",
    "pondicherry": "puducherry",
}

# Stripped before alias lookup so "Bengaluru, Karnataka" and
# "Bengaluru" fold together. Adzuna returns a comma-separated
# display_name whose tail is administrative rather than useful.
_NOISE_SUFFIXES = (" india", " in")


def normalize_location(value: str | None) -> str:
    """Fold a location string to its canonical key.

    Returns "" for None or blank, which callers treat as "unknown"
    rather than as a location that failed to match anything.
    """
    if not value:
        return ""

    cleaned = " ".join(value.strip().lower().split())
    if not cleaned:
        return ""

    # Adzuna's display_name is "City, State, Country" ordered
    # most-specific-first. The first segment is the one worth keeping;
    # the rest varies between postings for the same city and would
    # split one place into several hashes.
    head = cleaned.split(",")[0].strip()

    for suffix in _NOISE_SUFFIXES:
        if head.endswith(suffix):
            head = head[: -len(suffix)].strip()

    return LOCATION_ALIASES.get(head, head)
