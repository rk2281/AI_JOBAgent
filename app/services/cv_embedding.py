"""Embed the CV side, so a candidate can be compared against a job.

A separate module from job_embedding.py, sharing its shape but not its
code. Three things genuinely differ, and each one is a decision rather
than an accident:

  Task type. Jobs are embedded as RETRIEVAL_DOCUMENT, a CV as
  RETRIEVAL_QUERY -- the CV is the thing doing the searching. Measured
  on the live API: the same text under the two task types comes back
  with cosine 0.861247, so this is real work and not decoration. It
  exists to stop cosine similarity from mostly measuring "these are
  two different kinds of writing" instead of "this person fits this
  job".

  Batch size of one. embed_query() takes a single text because a query
  is asymmetric by nature. There are also very few rows -- one per
  profile -- so batching buys nothing.

  Truncation is real here. A job document is a title plus a
  500-character excerpt, about 550 characters against a budget of
  8000, so it can never truncate. A CV with five roles and a long
  summary can. This is the first place `truncated` will be non-zero,
  and if it is, that number should be looked at rather than accepted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.db.models.cv import CVVersion
from app.db.models.embedding import EmbeddingStatus
from app.db.repositories.cv import CVRepository
from app.db.repositories.job import EmbeddingRunRepository
from app.db.session import session_scope
from app.integrations.gemini_embeddings import (
    EmbeddingError,
    EmbeddingQuotaError,
    GeminiEmbeddingClient,
)
from app.services.embedding_text import build_cv_document, document_hash, fit_to_budget
from app.services.job_embedding import EmbeddingResult, _classify, _Counters

logger = logging.getLogger(__name__)

SCOPE_CV_VERSIONS = "cv_versions"


@dataclass(frozen=True)
class _PreparedDocument:
    """The embeddable text for one CV version, or None if it's empty.

    Shared between run_cv_embedding's batch loop and embed_cv_version's
    single-version path so the two never build the document
    differently. Does not call the API -- callers decide when: the
    batch loop rate-limits between calls, embed_cv_version makes
    exactly one.
    """

    version_id: int
    text: str | None
    digest: str | None
    truncated: bool


def _embed_one_version(version: CVVersion) -> _PreparedDocument:
    raw = build_cv_document(version.extracted_profile)
    text, was_truncated = fit_to_budget(raw)
    if not text.strip():
        return _PreparedDocument(version.id, None, None, was_truncated)
    return _PreparedDocument(version.id, text, document_hash(text), was_truncated)


@dataclass(frozen=True)
class CVEmbeddingOutcome:
    """What happened when embed_cv_version ran, for a caller that wants to react."""

    embedded: bool
    error: str | None = None


async def run_cv_embedding(
    client: GeminiEmbeddingClient | None = None,
    limit: int = 1000,
    retry_failed: bool = False,
) -> EmbeddingResult:
    """Embed every active CV version that needs it.

    Owns its own transactions, like run_job_embedding. Do not wrap in
    session_scope().
    """
    client = client or GeminiEmbeddingClient()
    counters = _Counters()
    truncated = 0
    started_at = datetime.now(timezone.utc)

    async with session_scope() as session:
        run = await EmbeddingRunRepository(session).start(
            SCOPE_CV_VERSIONS, client.model, started_at
        )
        run_id = run.id
        total_in_scope = await CVRepository(session).count_active_versions()

    pending: list[tuple[int, str, str]] = []

    async with session_scope() as session:
        repository = CVRepository(session)
        versions = await repository.list_active_versions_needing_embedding(
            limit, retry_failed
        )

        for version in versions:
            prepared = _embed_one_version(version)
            if prepared.truncated:
                truncated += 1

            counters.candidates_considered += 1

            if prepared.text is None:
                # An active version whose stored profile renders to
                # nothing. Not a provider failure -- our data. It
                # should be rare, because a version that failed the
                # emptiness check never becomes active_cv_version_id,
                # so a non-zero count here is worth investigating.
                counters.skipped_empty_text += 1
                continue

            pending.append((prepared.version_id, prepared.text, prepared.digest))

    status: EmbeddingStatus | None = None
    error_message: str | None = None

    for index, (version_id, text, digest) in enumerate(pending):
        if index > 0 and settings.embedding_seconds_between_calls > 0:
            await asyncio.sleep(settings.embedding_seconds_between_calls)

        counters.attempted += 1
        counters.api_calls += 1

        try:
            vector = await client.embed_query(text)
        except EmbeddingQuotaError as exc:
            # Stop rather than continue. Every remaining row would fail
            # the same way, and the rows already stored are safe
            # because each was committed on its own.
            counters.attempted -= 1
            status = EmbeddingStatus.QUOTA_EXCEEDED
            error_message = str(exc)
            logger.error("CV embedding stopped: quota exhausted.")
            break
        except EmbeddingError as exc:
            counters.failed += 1
            error_message = str(exc)
            async with session_scope() as session:
                await CVRepository(session).mark_version_embedding_failed(
                    version_id, str(exc)
                )
            continue

        counters.succeeded += 1
        async with session_scope() as session:
            await CVRepository(session).set_version_embedding(
                version_id, vector, client.model, digest, datetime.now(timezone.utc)
            )

    async with session_scope() as session:
        remaining_null = await CVRepository(
            session
        ).count_active_versions_missing_embedding()

    if status is None:
        status = _classify(counters, total_in_scope)

    aborted = status in (
        EmbeddingStatus.QUOTA_EXCEEDED,
        EmbeddingStatus.PROVIDER_ERROR,
    )

    if not counters.accounted_for():
        logger.error("CV embedding funnel does not balance: %s", asdict(counters))
    elif counters.abandoned_unexpectedly(aborted):
        logger.error(
            "CV embedding left %d candidate(s) unprocessed without aborting.",
            counters.abandoned,
        )

    async with session_scope() as session:
        await EmbeddingRunRepository(session).finish(
            run_id=run_id,
            status=status.value,
            finished_at=datetime.now(timezone.utc),
            counters=asdict(counters),
            remaining_null=remaining_null,
            error_message=error_message,
        )

    return EmbeddingResult(
        status=status,
        counters=counters,
        remaining_null=remaining_null,
        total_in_scope=total_in_scope,
        truncated=truncated,
        error_message=error_message,
    )


async def embed_cv_version(
    version_id: int,
    client: GeminiEmbeddingClient | None = None,
) -> CVEmbeddingOutcome:
    """Embed exactly one CV version, right after onboarding extracts it.

    Deliberately does not touch embedding_attempts on failure -- see
    CVRepository.record_embedding_error_without_attempt. A failure here
    must leave the row exactly as eligible for the next nightly
    embed_cvs sweep as a CV nobody has ever tried to embed, not opt it
    out after exactly one try. Do not unify this with run_cv_embedding's
    own failure handling.

    Owns its own transactions, like run_cv_embedding. Does not open an
    EmbeddingRunRepository row -- this is one call outside any
    scheduled run, not a run of its own.
    """
    client = client or GeminiEmbeddingClient()

    async with session_scope() as session:
        version = await CVRepository(session).version_by_id(version_id)

    if version is None:
        return CVEmbeddingOutcome(embedded=False, error="version not found")
    if version.embedding is not None:
        return CVEmbeddingOutcome(embedded=True)

    prepared = _embed_one_version(version)
    if prepared.text is None:
        return CVEmbeddingOutcome(embedded=False, error="empty document")

    try:
        vector = await client.embed_query(prepared.text)
    except (EmbeddingQuotaError, EmbeddingError) as exc:
        async with session_scope() as session:
            await CVRepository(session).record_embedding_error_without_attempt(
                version_id, str(exc)
            )
        return CVEmbeddingOutcome(embedded=False, error=str(exc))

    async with session_scope() as session:
        await CVRepository(session).set_version_embedding(
            version_id,
            vector,
            client.model,
            prepared.digest,
            datetime.now(timezone.utc),
        )
    return CVEmbeddingOutcome(embedded=True)
