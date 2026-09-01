"""Tests for the pure pieces of Day 7's similarity search.

No database and no network. search_by_vector, search_for_user,
search_by_text and self_check all open real sessions or call the
provider and are not exercised here -- what is pure is _to_similarity
and JobMatch, which is exactly what this file covers.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.job_search import DEFAULT_EF_SEARCH, JobMatch, _to_similarity


@pytest.mark.parametrize(
    ("distance", "expected_similarity"),
    [
        (0.0, 1.0),
        (1.0, 0.0),
        (0.25, 0.75),
        (1.5, 0.0),
        (2.0, 0.0),
    ],
)
def test_to_similarity(distance: float, expected_similarity: float) -> None:
    assert _to_similarity(distance) == pytest.approx(expected_similarity)


def test_clamping_protects_the_weighted_total_day_8_builds() -> None:
    """Day 8 folds `similarity` into a weighted sum alongside skills,
    location and experience. A negative component there would not
    merely rank that job last -- it would subtract from the other
    signals' contributions, making a job with a strong skill match but
    a poor semantic match score WORSE than a job with no skill match at
    all. Clamping at 0 is what keeps a bad semantic signal inert rather
    than actively harmful to the total."""
    assert _to_similarity(1.5) == 0.0
    assert _to_similarity(2.0) == 0.0


def test_default_ef_search_has_headroom_above_pgvectors_default() -> None:
    """40 is pgvector's own default for hnsw.ef_search -- the number of
    candidates the index keeps in flight while descending the graph.
    Asking for a top-10 with no headroom above that leaves the index
    almost no room to discard bad paths, which costs recall. Raising it
    is what search_by_vector and friends actually do by passing
    DEFAULT_EF_SEARCH through to JobRepository.nearest_to."""
    assert DEFAULT_EF_SEARCH > 40


def test_job_match_is_frozen() -> None:
    """Day 8 passes JobMatch instances around while building a weighted
    score. A similarity that could be edited in transit is a score
    nobody could trust or trace back to nearest_to()'s actual output."""
    match = JobMatch(
        job_id=1,
        title="Data Analyst",
        company="Acme",
        location="Gurgaon",
        distance=0.25,
        similarity=0.75,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        match.similarity = 0.99


def test_similarity_is_monotonically_decreasing_in_distance() -> None:
    """Nearest must mean highest. If similarity did not invert the
    order of distance, a search sorted by distance ascending would not
    also be sorted by similarity descending, and every caller that
    treats the first result as 'the best match' would be wrong."""
    distances = [0.1, 0.3, 0.2]
    similarities = [_to_similarity(d) for d in distances]

    ordered_by_distance = sorted(range(len(distances)), key=lambda i: distances[i])
    ordered_by_similarity_desc = sorted(
        range(len(similarities)), key=lambda i: -similarities[i]
    )

    assert ordered_by_distance == ordered_by_similarity_desc
