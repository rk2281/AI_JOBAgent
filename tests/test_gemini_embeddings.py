"""Tests for the embedding client's pure logic.

No network. The client's constructor makes no request, so it can be
built with a placeholder key, but no test here calls a method that
would.

What these guard is the set of things the isolate script found the
provider actually doing wrong on a neighbouring model: returning one
vector for eight inputs, and returning a vector that is not unit
length.

pytest-asyncio is not installed in this project and no other test uses
it, so the two async cases below drive their coroutine with
asyncio.run() inside a plain synchronous test function instead of
`@pytest.mark.asyncio`.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from app.core.config import settings
from app.integrations.gemini_embeddings import (
    EmbeddingResponseError,
    GeminiEmbeddingClient,
    _validate_vectors,
    describe_genai_error,
    is_quota_error,
    normalize,
)

SECRET = "AIzaSy-fake-key-value-abc123"


class FakeAPIError(Exception):
    def __init__(self, code: int, message: str, leaky: str, status: str = "") -> None:
        super().__init__(leaky)
        self.code = code
        self.message = message
        self.status = status


def test_normalize_produces_a_unit_vector() -> None:
    result = normalize([3.0, 4.0])
    assert math.sqrt(sum(v * v for v in result)) == pytest.approx(1.0)


def test_normalize_preserves_direction() -> None:
    """Normalising changes magnitude only. The measured 768-dimension
    output has norm 0.5856, and that magnitude is an artefact of
    truncation carrying no meaning."""
    result = normalize([1.0, 2.0])
    assert result[1] / result[0] == pytest.approx(2.0)


def test_normalize_rejects_a_zero_vector() -> None:
    """The worst thing that can reach the column: equidistant from
    everything, so its row lands at an arbitrary point in every
    ranking, while the column looks populated."""
    with pytest.raises(EmbeddingResponseError):
        normalize([0.0, 0.0, 0.0])


def test_validate_rejects_a_short_batch() -> None:
    """gemini-embedding-2 returned ONE vector for eight inputs. Not an
    error -- one vector. Code that zipped that onto rows would attach
    it to the first job and lose the other seven."""
    with pytest.raises(EmbeddingResponseError):
        _validate_vectors(
            [[1.0, 2.0]],
            expected_count=8,
            expected_dimension=2,
        )


def test_validate_rejects_a_wrong_dimension() -> None:
    with pytest.raises(EmbeddingResponseError):
        _validate_vectors(
            [[1.0, 2.0, 3.0]],
            expected_count=1,
            expected_dimension=768,
        )


def test_validate_accepts_a_correct_batch() -> None:
    _validate_vectors(
        [[1.0, 2.0], [3.0, 4.0]],
        expected_count=2,
        expected_dimension=2,
    )


def test_describe_reports_class_and_status_only() -> None:
    """This test exists because a provider error's string form was
    written into a database column live on Day 8 --
    "RateLimitError | Error code: 429 - {'error': ...}". The class and
    the status are enough to decide what to do; the body never is,
    and on the enrichment path that body can contain job text.
    """
    error = FakeAPIError(429, "secret-job-text-should-not-appear", f"...key={SECRET}")
    described = describe_genai_error(error)

    assert "FakeAPIError" in described
    assert "429" in described
    assert "secret-job-text-should-not-appear" not in described


def test_describe_never_carries_the_exceptions_string_form() -> None:
    error = FakeAPIError(401, "Unauthorized", f"...key={SECRET}")
    described = describe_genai_error(error)
    assert SECRET not in described
    assert "key=" not in described


def test_the_raw_exception_really_does_leak() -> None:
    """Guards the premise, so the test above cannot start passing for
    the wrong reason."""
    assert SECRET in str(FakeAPIError(401, "Unauthorized", f"...key={SECRET}"))


def test_quota_error_detected_by_code() -> None:
    assert is_quota_error(FakeAPIError(429, "", "")) is True


def test_quota_error_detected_by_status() -> None:
    assert is_quota_error(FakeAPIError(0, "", "", status="RESOURCE_EXHAUSTED")) is True


def test_other_errors_are_not_quota_errors() -> None:
    assert is_quota_error(FakeAPIError(500, "", "")) is False
    assert is_quota_error(ValueError("nope")) is False


def test_client_reads_its_model_and_dimension_from_settings() -> None:
    """The service writes client.model into embedding_model on every
    row, so it has to be readable back."""
    client = GeminiEmbeddingClient(api_key="placeholder-not-used")
    assert client.model == settings.gemini_embedding_model
    assert client.dimension == settings.embedding_dimension


def test_empty_batch_makes_no_request() -> None:
    """No call means no quota spent. An empty batch is the caller's
    bookkeeping question, not a request."""
    client = GeminiEmbeddingClient(api_key="placeholder-not-used")

    async def scenario() -> list[list[float]]:
        return await client.embed_documents([])

    assert asyncio.run(scenario()) == []


def test_blank_document_is_refused_before_any_request() -> None:
    """A blank document is our data gap, not the provider's failure.
    The service counts it as skipped; the client refuses it so that a
    miscount cannot turn into a wasted call."""
    client = GeminiEmbeddingClient(api_key="placeholder-not-used")

    async def scenario() -> None:
        await client.embed_documents(["   "])

    with pytest.raises(ValueError):
        asyncio.run(scenario())
