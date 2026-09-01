"""Tests for the pure helpers in scripts.embedding_isolate.

Three things are worth testing here and nothing else is:

  - describe_genai_error must not carry the exception's own string
    form into the output. A provider error can echo back the request
    that produced it, and on Day 7 that request is a CV.

  - l2_norm and cosine_similarity are what Day 7 uses to decide
    whether a returned vector is storable. A zero vector must be
    reported as a number rather than raising, because it is a real
    failure mode: it sits equidistant from everything and would place
    its row at an arbitrary point in every ranking while looking
    perfectly valid in the column.
"""

from __future__ import annotations

import math

import pytest

from scripts.embedding_isolate import (
    cosine_similarity,
    describe_genai_error,
    l2_norm,
)

SECRET = "AIzaSy-fake-key-value-abc123"


class FakeAPIError(Exception):
    """Shaped like a google-genai APIError: code, message, and a leaky str()."""

    def __init__(self, code: int, message: str, leaky: str) -> None:
        super().__init__(leaky)
        self.code = code
        self.message = message


def test_describe_reports_the_code_and_message() -> None:
    error = FakeAPIError(429, "Quota exceeded", f"request to ...key={SECRET}")
    described = describe_genai_error(error)
    assert "429" in described
    assert "Quota exceeded" in described


def test_describe_never_carries_the_exceptions_string_form() -> None:
    error = FakeAPIError(401, "Unauthorized", f"request to ...key={SECRET}")
    described = describe_genai_error(error)
    assert SECRET not in described
    assert "key=" not in described


def test_describe_handles_a_plain_exception() -> None:
    described = describe_genai_error(ValueError("something"))
    assert "ValueError" in described


def test_the_raw_exception_really_does_leak() -> None:
    """Guards the premise. If str() stopped leaking, the tests above
    would pass for the wrong reason and quietly stop testing anything."""
    error = FakeAPIError(401, "Unauthorized", f"request to ...key={SECRET}")
    assert SECRET in str(error)


@pytest.mark.parametrize(
    ("vector", "expected"),
    [
        ([1.0, 0.0, 0.0], 1.0),
        ([3.0, 4.0], 5.0),
        ([0.0, 0.0], 0.0),
    ],
)
def test_l2_norm(vector: list[float], expected: float) -> None:
    assert l2_norm(vector) == pytest.approx(expected)


def test_cosine_of_identical_vectors_is_one() -> None:
    vector = [0.1, 0.2, 0.3, 0.4]
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_ignores_magnitude() -> None:
    """The reason an unnormalised vector is still safe with the <=>
    operator: cosine divides magnitude out."""
    assert cosine_similarity([1.0, 2.0], [10.0, 20.0]) == pytest.approx(1.0)


def test_cosine_with_a_zero_vector_returns_zero_rather_than_raising() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_length_mismatch_raises() -> None:
    """zip(strict=True) on purpose. Two vectors of different lengths
    are never comparable, and silently truncating to the shorter one
    would return a plausible number for an impossible comparison."""
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
