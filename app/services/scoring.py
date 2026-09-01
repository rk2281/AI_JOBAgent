"""Combine the five signals into one score, traceably.

This module has exactly two things it must never do: score a missing
signal as zero, and produce a number nobody can trace back to its
inputs. Every other decision here -- the quality multiplier, the
weight renormalisation, the reasons list, the fingerprint -- exists in
service of one or the other of those two rules.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import settings
from app.services.locations import normalize_location
from app.services.scoring_signals import COUNTRY_ONLY_LOCATIONS, SignalScore


@dataclass(frozen=True)
class QualityAssessment:
    """How much to discount a score for the posting's own trustworthiness.

    Returned as its own object, separately from the reasons plain
    text, so a caller can increment scoring_runs.quality_penalty_agency
    and quality_penalty_no_city from is_agency / missing_city directly
    rather than re-deriving those booleans by re-parsing reasons.
    """

    multiplier: float
    reasons: list[str]
    is_agency: bool
    missing_city: bool


def assess_quality(company: str | None, location: str | None) -> QualityAssessment:
    """Score how much to trust a posting's own description of itself.

    A MULTIPLIER, not a sixth weighted signal. A signal answers "does
    this person fit this job"; this answers "is this posting a
    trustworthy description of one" -- a different axis entirely. A
    staffing agency's listing is not a worse fit, it is a less
    reliable account of one, and folding that into the weights would
    make it impossible to explain to a user why their score moved.

    Company is matched by EXACT normalized equality against
    settings.staffing_agency_list, not substring. Substring matching
    would fire "meta" against "Metadata Solutions" and silently
    penalise the wrong job, with nothing anywhere reporting it. Exact
    matching instead misses spelling variants like "Vrinda
    International Pvt Ltd" -- but that miss shows up as a lower
    quality_penalty_agency count in scoring_runs, somewhere a person
    can actually see it. A loud miss beats a silent false positive.

    Both penalties can apply to the same job, and multiply together
    rather than stack additively, so the result can never go negative.
    """
    multiplier = 1.0
    reasons: list[str] = []
    is_agency = False
    missing_city = False

    normalized_company = (company or "").strip().lower()
    if normalized_company in settings.staffing_agency_list:
        multiplier *= settings.quality_multiplier_agency
        reasons.append("posted by a staffing agency")
        is_agency = True

    normalized_location = normalize_location(location)
    if normalized_location == "" or normalized_location in COUNTRY_ONLY_LOCATIONS:
        multiplier *= settings.quality_multiplier_no_city
        reasons.append("no city given")
        missing_city = True

    return QualityAssessment(
        multiplier=multiplier,
        reasons=reasons,
        is_agency=is_agency,
        missing_city=missing_city,
    )


@dataclass(frozen=True)
class ScoredPair:
    """The full, traceable result of scoring one candidate-job pair."""

    semantic: SignalScore
    skill: SignalScore
    experience: SignalScore
    location: SignalScore
    title: SignalScore
    semantic_raw: float | None
    weight_covered: float
    weighted_total: float
    quality: QualityAssessment
    final_score: float
    match_reasons: list[str] = field(default_factory=list)


def combine(
    *,
    semantic: SignalScore,
    skill: SignalScore,
    experience: SignalScore,
    location: SignalScore,
    title: SignalScore,
    semantic_raw: float | None,
    quality: QualityAssessment,
) -> ScoredPair:
    """Weight, renormalise and multiply five signals into one score.

    Renormalising by weight_covered is what makes a job with no
    extractable skills rankable at all, instead of being permanently
    stuck near the bottom for a data gap rather than a fit gap.
    weight_covered is stored on the row rather than discarded, because
    a score built from 35% of the weight is not comparable with one
    built from 100% -- two recommendations rows can carry the same
    final_score for very different reasons, and this is what tells
    them apart.

    weight_covered == 0.0 is a real, expected state, not an error
    path: every signal abstained, most likely because neither the CV
    nor the job carry any of the data the signals need. It gets its
    own reason as the FIRST entry in match_reasons, because a
    final_score of 0.0 otherwise has two indistinguishable causes --
    "everything scored zero" and "nothing could be scored" -- and a
    stored row must be able to tell those apart on its own, without
    anyone re-running the computation to find out which one happened.

    Abstained signals are listed in match_reasons too, prefixed
    "abstained: ". An explanation that only names what matched is an
    explanation that hides why the score is as low as it is.
    """
    weighted_signals = (
        (semantic, settings.weight_semantic),
        (skill, settings.weight_skill),
        (experience, settings.weight_experience),
        (location, settings.weight_location),
        (title, settings.weight_title),
    )

    weight_covered = sum(weight for signal, weight in weighted_signals if signal.value is not None)

    if weight_covered == 0.0:
        weighted_total = 0.0
    else:
        weighted_total = (
            sum(weight * signal.value for signal, weight in weighted_signals if signal.value is not None)
            / weight_covered
        )

    final_score = weighted_total * quality.multiplier

    non_abstained_reasons = [signal.reason for signal, _ in weighted_signals if signal.value is not None]
    abstained_reasons = [
        f"abstained: {signal.reason}" for signal, _ in weighted_signals if signal.value is None
    ]
    match_reasons = non_abstained_reasons + abstained_reasons + list(quality.reasons)

    if weight_covered == 0.0:
        match_reasons = ["no signal had data"] + match_reasons

    return ScoredPair(
        semantic=semantic,
        skill=skill,
        experience=experience,
        location=location,
        title=title,
        semantic_raw=semantic_raw,
        weight_covered=weight_covered,
        weighted_total=weighted_total,
        quality=quality,
        final_score=final_score,
        match_reasons=match_reasons,
    )


def compute_inputs_fingerprint(
    *,
    profile_updated_at: datetime,
    profile_skills: list[str],
    job_embedding_source_hash: str | None,
    job_skills_source_hash: str | None,
    weights_version: int,
) -> str:
    """SHA-256 over everything that fed one score.

    profile_skills is SORTED before hashing so that a reordering which
    changes nothing about the candidate does not read as a change in
    inputs.

    Same idea as jobs.embedding_source_hash, and the same caveat
    applies here too: this does NOT catch a job posting changing at
    the source, because a stored job's text never changes after
    insert. What it catches is OUR inputs and OUR rules changing --
    the profile being re-extracted, or the weights being re-tuned.
    """
    parts = [
        profile_updated_at.isoformat(),
        ",".join(sorted(profile_skills)),
        job_embedding_source_hash or "",
        job_skills_source_hash or "",
        str(weights_version),
    ]
    digest_input = "|".join(parts).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def rank(pairs: list[tuple[int, ScoredPair]]) -> list[tuple[int, ScoredPair, int]]:
    """Sort by final_score descending and assign 1-based ranks.

    Ties are broken by job_id ascending -- stated explicitly here
    rather than left to the sort's stability. An unstated tiebreak
    means a re-run over the same data can reorder identical scores
    depending on incidental input order, and a rank that moves without
    any underlying cause is untraceable, which is exactly what this
    module exists to avoid.
    """
    ordered = sorted(pairs, key=lambda pair: (-pair[1].final_score, pair[0]))
    return [(job_id, scored, position) for position, (job_id, scored) in enumerate(ordered, start=1)]
