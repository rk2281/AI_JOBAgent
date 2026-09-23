"""embed_cv_version -- the onboarding-time, single-CV embed path.

A real PostgreSQL, a fake Gemini embedding client. Mirrors the shape of
tests/integration/test_candidate_pipeline.py: the only substitution is
the paid network call.

THE PROPERTY MOST OF THIS FILE IS ABOUT

A failure here must NOT increment embedding_attempts. run_cv_embedding's
own batch failures do (via mark_version_embedding_failed) and that is
unchanged -- but embed_cv_version calls
record_embedding_error_without_attempt instead, specifically so an
onboarding-time failure leaves the row at embedding_attempts == 0 and
still eligible for the next nightly embed_cvs sweep, rather than opting
it out after exactly one try. See both functions' docstrings.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from app.db.models.cv import CV, CVVersion, ExtractionStatus
from app.db.models.user import User
from app.db.session import session_scope
from app.integrations.gemini_embeddings import EmbeddingError, EmbeddingQuotaError
from app.services.cv_embedding import embed_cv_version


class FakeGeminiEmbeddingClient:
    """Stands in for the paid embedding call. Nothing else is faked."""

    model = "fake-embedding-model"

    def __init__(self, *, vector: list[float] | None = None, raises: Exception | None = None) -> None:
        self._vector = vector if vector is not None else [0.1] * 768
        self._raises = raises
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._raises is not None:
            raise self._raises
        return self._vector


REALISTIC_PROFILE = {
    "current_title": "ML Engineer",
    "skills": ["Python", "PyTorch"],
    "summary": "Builds and ships ML systems end to end.",
}


async def _make_cv_version(
    *, telegram_id: int, extracted_profile: dict[str, Any]
) -> int:
    """Insert User -> CV -> CVVersion and return the version's id.

    No Profile row: embed_cv_version takes a version_id directly and
    never joins through profiles, unlike the nightly sweep's own query.
    """
    async with session_scope() as session:
        user = User(telegram_id=telegram_id, full_name="Test Candidate")
        session.add(user)
        await session.flush()

        cv = CV(
            user_id=user.id,
            file_name="cv.docx",
            file_type="docx",
            storage_path="unused",
            extraction_status=ExtractionStatus.COMPLETE.value,
        )
        session.add(cv)
        await session.flush()

        version = CVVersion(
            cv_id=cv.id,
            version=1,
            extracted_profile=extracted_profile,
        )
        session.add(version)
        await session.flush()
        return version.id


async def _load_version(version_id: int) -> CVVersion:
    async with session_scope() as session:
        return (
            await session.execute(select(CVVersion).where(CVVersion.id == version_id))
        ).scalar_one()


def test_a_successful_embed_is_stored_and_counted(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    client = FakeGeminiEmbeddingClient(vector=[0.25] * 768)

    async def body() -> dict[str, Any]:
        version_id = await _make_cv_version(
            telegram_id=910001, extracted_profile=REALISTIC_PROFILE
        )
        outcome = await embed_cv_version(version_id, client=client)
        row = await _load_version(version_id)
        return {"outcome": outcome, "row": row}

    observed = run_with_database(body)

    assert observed["outcome"].embedded is True
    assert observed["outcome"].error is None
    assert client.calls  # the fake was actually invoked

    row = observed["row"]
    assert row.embedding is not None
    assert row.embedding_model == "fake-embedding-model"
    assert row.embedded_at is not None
    assert row.embedding_error is None
    # Success still counts as an attempt -- it's terminal (embedding
    # IS NOT NULL excludes it from every future sweep regardless), so
    # there is nothing for the attempts count to gate here.
    assert row.embedding_attempts == 1


def test_a_missing_version_id_is_reported_not_raised(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    client = FakeGeminiEmbeddingClient()

    async def body() -> Any:
        return await embed_cv_version(999_999, client=client)

    outcome = run_with_database(body)

    assert outcome.embedded is False
    assert outcome.error == "version not found"
    assert client.calls == []  # no API call was made for a row that doesn't exist


def test_an_empty_document_is_our_data_not_the_providers(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """extracted_profile={} renders to "" via build_cv_document, the
    same case run_cv_embedding counts as skipped_empty_text rather than
    a failure. No API call, no write of any kind -- the row must come
    back byte-for-byte as it went in."""
    client = FakeGeminiEmbeddingClient()

    async def body() -> dict[str, Any]:
        version_id = await _make_cv_version(telegram_id=910002, extracted_profile={})
        outcome = await embed_cv_version(version_id, client=client)
        row = await _load_version(version_id)
        return {"outcome": outcome, "row": row}

    observed = run_with_database(body)

    assert observed["outcome"].embedded is False
    assert observed["outcome"].error == "empty document"
    assert client.calls == []

    row = observed["row"]
    assert row.embedding is None
    assert row.embedding_error is None
    assert row.embedding_attempts == 0


def test_a_quota_error_is_recorded_without_counting_as_an_attempt(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """The property this whole design turns on: embedding_attempts must
    stay at 0 after a failure here, or the next nightly embed_cvs sweep
    (which filters on attempts == 0) will never see this row again.

    EmbeddingError (the non-quota case) is caught by the exact same
    `except (EmbeddingQuotaError, EmbeddingError)` branch in
    embed_cv_version, since EmbeddingQuotaError subclasses it -- one
    test of the shared branch is sufficient; a second exercising the
    base class would be testing Python's own exception matching, not
    this code.
    """
    client = FakeGeminiEmbeddingClient(raises=EmbeddingQuotaError("quota gone"))

    async def body() -> dict[str, Any]:
        version_id = await _make_cv_version(
            telegram_id=910003, extracted_profile=REALISTIC_PROFILE
        )
        outcome = await embed_cv_version(version_id, client=client)
        row = await _load_version(version_id)
        return {"outcome": outcome, "row": row}

    observed = run_with_database(body)

    assert observed["outcome"].embedded is False
    assert observed["outcome"].error == "quota gone"

    row = observed["row"]
    assert row.embedding is None
    # The error IS recorded, for diagnosis --
    assert row.embedding_error == "quota gone"
    # -- but the attempt is NOT counted. This is the whole point of
    # record_embedding_error_without_attempt over mark_version_embedding_failed:
    # attempts stays exactly where it started, not 0 -> 1.
    assert row.embedding_attempts == 0


def test_an_already_embedded_version_is_left_alone(
    run_with_database: Callable[[Callable[[], Awaitable[Any]]], Any],
) -> None:
    """A second call (e.g. a re-run onboarding task) must not spend a
    second API call or overwrite a good vector."""
    client = FakeGeminiEmbeddingClient()

    async def body() -> dict[str, Any]:
        version_id = await _make_cv_version(
            telegram_id=910004, extracted_profile=REALISTIC_PROFILE
        )
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(CVVersion).where(CVVersion.id == version_id)
                )
            ).scalar_one()
            row.embedding = [0.9] * 768
            row.embedding_model = "already-here"

        outcome = await embed_cv_version(version_id, client=client)
        return {"outcome": outcome}

    observed = run_with_database(body)

    assert observed["outcome"].embedded is True
    assert observed["outcome"].error is None
    assert client.calls == []
