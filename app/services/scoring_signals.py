"""The five matching signals, each in isolation.

Every function here returns a SignalScore whose `value` is
`float | None`. None means ABSTAIN -- "we could not look" -- and 0.0
means "we looked and it did not match". That distinction is the whole
design of Day 8. Storing an abstain as 0.0 would rank every non-tech
job below every tech job for a reason that is a data gap, not a fit
gap: a job with no extractable skills would score 0.0 on 30% of the
model through no fault of its own, and nothing anywhere would say so.

Nothing here touches a database, an API client, or app.core.config's
Settings object directly beyond reading a few scalar values. These
are rules, not lookups, and they are kept pure specifically so every
branch can be tested exhaustively with plain inputs and no fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.locations import normalize_location

COUNTRY_ONLY_LOCATIONS = frozenset({"india"})


@dataclass(frozen=True)
class SignalScore:
    """One signal's verdict on one candidate-job pair.

    `reason` is a short human-readable phrase, never omitted -- it
    becomes part of recommendations.match_reasons, the plan sheet's
    "match explanation" deliverable. Every return path in this module
    sets one, abstains included. An unexplained abstain is the same
    invisible failure this project keeps paying for: a score that is
    low for a reason nobody wrote down.
    """

    value: float | None
    reason: str


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_semantic(raw_similarity: float | None) -> SignalScore:
    """Rescale raw cosine similarity onto 0-1 against FIXED anchors.

    Fixed anchors (settings.semantic_anchor_low/high), not min-max
    over the day's candidate set. Candidate-set rescaling would make
    a stored score irreproducible: recommendations has a unique
    constraint on (user_id, job_id), and a score computed relative to
    that day's neighbours was never a property of the pair. It was a
    property of the batch -- the same defect that made JobMatch
    frozen in the first place.

    A raw value exactly equal to semantic_anchor_high maps to 1.0 by
    plain arithmetic, WITHOUT being clamped:
    (high - low) / (high - low) is 1.0 on its own. Callers counting
    how often clamping occurred must compare with strict `raw > high`
    and `raw < low` -- see semantic_clamp_flags -- or they will count
    a boundary value as a loss of discrimination that never happened.
    """
    if raw_similarity is None:
        return SignalScore(None, "no embedding on one side")

    low = settings.semantic_anchor_low
    high = settings.semantic_anchor_high
    scaled = (raw_similarity - low) / (high - low)
    value = _clamp(scaled, 0.0, 1.0)

    return SignalScore(value, f"semantic similarity {raw_similarity:.4f} raw")


def semantic_clamp_flags(raw: float | None) -> tuple[bool, bool]:
    """(clamped_low, clamped_high), strictly compared.

    A separate function rather than inlining this at every call site,
    so the score and the clamp counters cannot drift apart -- both
    read the same anchors the same way.
    """
    if raw is None:
        return False, False

    low = settings.semantic_anchor_low
    high = settings.semantic_anchor_high
    return raw < low, raw > high


def score_skill(job_skills: list[str], profile_skills: list[str]) -> SignalScore:
    """Fraction of the job's required skills the candidate holds.

    Both lists are assumed already normalized catalog keys. The
    denominator is the JOB's skill count, not the union and not the
    candidate's. The question this signal answers is "how much of
    what this job asks for does this person have" -- a candidate
    holding fifty skills should not score higher on a job that only
    asks for three just because their side of the set is larger.
    """
    if not job_skills:
        return SignalScore(None, "job lists no extractable skills")
    if not profile_skills:
        return SignalScore(None, "profile has no skills")

    job_set = set(job_skills)
    profile_set = set(profile_skills)
    overlap = job_set & profile_set

    value = len(overlap) / len(job_set)
    return SignalScore(value, f"{len(overlap)} of {len(job_set)} required skills")


def score_experience(
    candidate_years: float | None,
    job_min: int | None,
    job_max: int | None,
) -> SignalScore:
    """Taper toward a job's required experience range.

    candidate_years is None means UNKNOWN -- we have no figure for
    this candidate. 0.0 means a genuine fresher. Treating those the
    same is wrong in both directions, and it is the same distinction
    compute_total_experience_years() returns None for on the
    candidate side rather than guessing 0.

    x == job_min and x == job_max both score exactly 1.0. The range
    is inclusive at both ends, and that is tested at exactly those
    values rather than near them.

    Overqualified (x > job_max) is 1.0, not a penalty. Whether to
    apply for a job below one's level is the user's call, not ours to
    make for them.

    A NULL job_max with job_min present ("5+ years") is treated as an
    open-ended range -- infinity, not an abstain. The posting told us
    something real; it just did not put a ceiling on it.
    """
    if candidate_years is None:
        return SignalScore(None, "candidate experience unknown")
    if job_min is None and job_max is None:
        return SignalScore(None, "job states no experience requirement")

    lo = 0.0 if job_min is None else float(job_min)
    hi = float("inf") if job_max is None else float(job_max)
    x = candidate_years

    if lo <= x <= hi:
        return SignalScore(1.0, "meets required experience range")

    if x > hi:
        return SignalScore(1.0, "more experience than required")

    taper = settings.experience_taper_years
    value = max(0.0, 1.0 - (lo - x) / taper)
    return SignalScore(value, f"{lo - x:.1f} years short of the {lo:.0f}-year minimum")


def score_location(
    job_location: str | None,
    job_work_mode: str | None,
    preferred_locations: list[str],
    remote_only: bool,
) -> SignalScore:
    """Compare a job's city against a candidate's preferred cities.

    23 of the 99 stored jobs carry location "India" with no city at
    all. Scoring those 0.0 would punish a job for a gap in the
    source's own data, not for being a bad fit. That case is an
    ABSTAIN here (step 5 below); the quality multiplier in scoring.py
    is where that data gap actually costs the job something, and it
    costs it in a place someone reading scoring_runs can see.

    Step 7 -- hybrid in a different city -- is the only signal in this
    module that returns a middle value from a rule rather than a
    formula. A hybrid role in another city is genuinely partial, not
    a clean zero or one, and that is stated here rather than left for
    someone to discover by reading the number.

    The order below is significant and is implemented exactly as
    listed:

      1. remote role -> 1.0, unconditionally
      2. remote_only preference and the role is not remote -> 0.0
      3. no preferred_locations at all -> abstain
      4. job location does not normalize to anything -> abstain
      5. job location normalizes to a country-only value -> abstain
      6. normalized job location matches a preferred location -> 1.0
      7. hybrid role, no match -> 0.5
      8. otherwise -> 0.0
    """
    if job_work_mode == "remote":
        return SignalScore(1.0, "remote role")

    if remote_only and job_work_mode != "remote":
        return SignalScore(0.0, "user wants remote only")

    if not preferred_locations:
        return SignalScore(None, "no location preference set")

    normalized_job = normalize_location(job_location)
    if normalized_job == "":
        return SignalScore(None, "job location unknown")

    if normalized_job in COUNTRY_ONLY_LOCATIONS:
        return SignalScore(None, "job location is country-level only")

    normalized_prefs = {normalize_location(p) for p in preferred_locations}
    if normalized_job in normalized_prefs:
        return SignalScore(1.0, "preferred location match")

    if job_work_mode == "hybrid":
        return SignalScore(0.5, "hybrid role in a different city")

    return SignalScore(0.0, "different city")


def _tokenize_title(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if token not in settings.title_weak_token_set}


def score_title(job_title: str | None, target_roles: list[str]) -> SignalScore:
    """Token overlap between a job's title and the user's target roles.

    "Senior Engineer" against "Lead Developer" tells us nothing in
    either direction once the generic words are stripped -- both
    reduce to nothing. Scoring that 0.0 would punish a job for having
    a common title, so it abstains instead.

    Uses the MAX overlap across target_roles, not the mean. A user
    listing three target roles wants a job matching ANY of them, and
    averaging would penalise someone for having broad interests.
    """
    title_tokens = _tokenize_title(job_title or "")
    if not title_tokens:
        return SignalScore(None, "job title is generic")

    if not target_roles:
        return SignalScore(None, "no target roles set")

    role_token_sets = [_tokenize_title(role) for role in target_roles if role]
    nonempty_role_sets = [tokens for tokens in role_token_sets if tokens]
    if not nonempty_role_sets:
        return SignalScore(None, "target roles are generic")

    best = 0.0
    for role_tokens in nonempty_role_sets:
        overlap = role_tokens & title_tokens
        best = max(best, len(overlap) / len(role_tokens))

    # The reason has to branch on the value. Day 12 found a stored
    # recommendation reading `title_score = 0.0` alongside
    # "title overlaps a target role" -- for "NLP Engineer" against
    # ["AI Engineer", "ML Engineer", "Machine Learning Engineer"],
    # where every shared word is a weak token and the real overlap is
    # empty. The NUMBER was right; the sentence next to it was not.
    #
    # This matters more than it looks. match_reasons is the "match
    # explanation" a user is shown, so the wrong text here is a claim
    # made to a person, not an internal label -- and it is invisible
    # unless somebody reads the reason and the score in the same
    # query, which is how it was found.
    #
    # Only the string changes. The value, the weight, weight_covered
    # and every gate are untouched: a 0.0 title score still SCORES
    # zero rather than abstaining, because "we compared and they do
    # not match" is a real answer and turning it into an abstain would
    # be a change to the model.
    if best == 0.0:
        return SignalScore(0.0, "title shares no words with any target role")

    return SignalScore(best, "title overlaps a target role")
