"""Turn stored jobs into vectors, in a way that cannot quietly do nothing.

Runs as a separate pass rather than inside ingestion, keeping the
separation Day 6 built deliberately when it left `embedding` nullable.
Three reasons, and the first is the one that matters:

  Ingestion and embedding are rationed by different quotas. Adzuna
  allows a fixed number of calls per month and those calls are already
  spent by the time embedding would begin. Letting a Gemini 429 abort
  a run whose Adzuna budget is gone trades a cheap, repeatable failure
  for an expensive, unrepeatable one.

  This pass is idempotent and re-runnable. Ingestion is not. Coupling
  them would bind the safe thing to the unsafe one.

  Changing the model, or changing build_job_document(), requires
  re-embedding every row WITHOUT re-ingesting. If embedding only
  happened inside ingestion, that would be impossible.

Owns its own transactions, like run_ingestion. Do not wrap calls to
run_job_embedding() in session_scope() -- it opens one per batch on
purpose, so that an interruption at row 60 keeps the first 59 rather
than discarding 98 successful API calls.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.core.config import settings
from app.db.models.embedding import EmbeddingStatus
from app.db.repositories.job import EmbeddingRunRepository, JobRepository
from app.db.session import session_scope
from app.integrations.gemini_embeddings import (
    EmbeddingError,
    EmbeddingQuotaError,
    GeminiEmbeddingClient,
    describe_genai_error,
)
from app.services.embedding_text import build_job_document, document_hash, fit_to_budget

logger = logging.getLogger(__name__)

SCOPE_JOBS = "jobs"


@dataclass
class _Counters:
    """A funnel, not a summary.

    The arithmetic is asserted rather than assumed, because a lost row
    otherwise shows up only as a number being slightly lower than
    someone expected, which nobody notices.

    The first live run proved the point in an unexpected direction:
    the assertion fired, and the fault was in the model behind it, not
    in the data. It assumed every candidate ends up either skipped or
    attempted. A quota abort breaks out of the loop with candidates
    still unprocessed, and those are neither -- there was no name for
    them. `abandoned` is that name.
    """

    candidates_considered: int = 0
    skipped_empty_text: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    api_calls: int = 0

    @property
    def abandoned(self) -> int:
        """Candidates selected but never sent, because the run stopped early.

        Derived rather than stored. It needs no column: it is exactly
        what the other three counters leave over, so persisting it
        would create a second copy of a number that can disagree with
        the first. A migration for a subtraction is not worth having.
        """
        return self.candidates_considered - self.skipped_empty_text - self.attempted

    def accounted_for(self) -> bool:
        """Whether every selected row can be traced to an outcome.

        Deliberately does NOT require abandoned == 0. An aborted run
        legitimately leaves rows untouched, and treating that as a
        corrupt funnel would cry wolf on the one path that already
        reports itself honestly. What must never happen is a NEGATIVE
        abandoned count -- that means more rows were processed than
        were selected -- or an attempted count that does not split
        cleanly into successes and failures.
        """
        return (
            self.abandoned >= 0
            and self.attempted == self.succeeded + self.failed
        )

    def abandoned_unexpectedly(self, aborted: bool) -> bool:
        """Rows left behind by a run that did NOT stop early.

        This is the condition worth shouting about. If nothing aborted
        and rows still went missing between selection and sending,
        something dropped them silently.
        """
        return self.abandoned > 0 and not aborted


@dataclass
class EmbeddingResult:
    """What one pass did. Returned to the caller and printed by the script."""

    status: EmbeddingStatus
    counters: _Counters = field(default_factory=_Counters)
    remaining_null: int = 0
    total_in_scope: int = 0
    truncated: int = 0
    error_message: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.status in (EmbeddingStatus.COMPLETE, EmbeddingStatus.NOTHING_TO_DO)


def _classify(counters: _Counters, total_in_scope: int) -> EmbeddingStatus:
    """Which kind of "nothing" was it?

    The whole reason this function exists. A pass that embedded zero
    rows has several meanings and only one of them is fine:

      nothing needed doing, and rows exist   -> NOTHING_TO_DO  (fine)
      nothing needed doing, and no rows do   -> NO_SOURCE_ROWS (broken)

    Those two are the pair a single "0 rows embedded" log line would
    merge, and they mean opposite things: work finished, versus
    nothing to work on. On a table that should hold 99 active jobs,
    the second means ingestion or the eligibility filter is broken.
    """
    if counters.candidates_considered == 0:
        return (
            EmbeddingStatus.NO_SOURCE_ROWS
            if total_in_scope == 0
            else EmbeddingStatus.NOTHING_TO_DO
        )

    if counters.attempted == 0:
        # Candidates existed but every one produced empty text. Not a
        # provider failure; a gap in our own data.
        # skipped_empty_text == candidates_considered identifies it.
        return EmbeddingStatus.ALL_FAILED

    if counters.succeeded == 0:
        return EmbeddingStatus.ALL_FAILED

    if counters.failed > 0:
        # Not folded into COMPLETE. A run where half the rows failed
        # is not a completed run, and calling it one files the
        # failures behind the word people scan past.
        return EmbeddingStatus.PARTIAL

    return EmbeddingStatus.COMPLETE


def _chunks(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


async def run_job_embedding(
    client: GeminiEmbeddingClient | None = None,
    limit: int = 1000,
    retry_failed: bool = False,
    recheck: bool = False,
) -> EmbeddingResult:
    """Embed every active job that needs it.

    `client` is injectable so that tests can pass a stub. The default
    builds the real one.

    `recheck` compares each active row's stored embedding_source_hash
    against what build_job_document() produces today, and re-embeds
    the mismatches. Worth being precise about what that catches,
    because the obvious reading is wrong: it does NOT catch the source
    changing a posting. compute_content_hash() covers title, company
    and location only, and mark_seen() refreshes nothing, so a stored
    job's text never changes after insert. What it catches is OUR rule
    changing -- the day build_job_document() gains a field or alters
    its format, every stored vector silently becomes stale while
    continuing to look valid.
    """
    client = client or GeminiEmbeddingClient()
    counters = _Counters()
    truncated = 0
    started_at = datetime.now(timezone.utc)

    async with session_scope() as session:
        run = await EmbeddingRunRepository(session).start(
            SCOPE_JOBS, client.model, started_at
        )
        run_id = run.id
        total_in_scope = await JobRepository(session).count_active_jobs()

    # (job_id, text, hash) for everything that will be sent.
    pending: list[tuple[int, str, str]] = []

    async with session_scope() as session:
        repository = JobRepository(session)
        rows = (
            await repository.list_active_for_recheck(limit)
            if recheck
            else await repository.list_needing_embedding(limit, retry_failed)
        )

        for job in rows:
            raw = build_job_document(job.title, job.description)
            text, was_truncated = fit_to_budget(raw)
            if was_truncated:
                truncated += 1

            if not text.strip():
                # Our data gap, not the provider's failure. Counted
                # separately so that "we had nothing to send" never
                # reads as "the provider refused".
                counters.candidates_considered += 1
                counters.skipped_empty_text += 1
                continue

            digest = document_hash(text)

            if recheck and job.embedding is not None and job.embedding_source_hash == digest:
                # Already current. Not a candidate at all, so it does
                # not enter the funnel.
                continue

            counters.candidates_considered += 1
            pending.append((job.id, text, digest))

    status: EmbeddingStatus | None = None
    error_message: str | None = None

    batches = _chunks(pending, settings.embedding_batch_size)

    for index, batch in enumerate(batches):
        if index > 0 and settings.embedding_seconds_between_calls > 0:
            # Paced because the quota is per-minute. Skipped before the
            # first call -- waiting to make a request nobody has
            # rate-limited yet is pure delay.
            await asyncio.sleep(settings.embedding_seconds_between_calls)

        texts = [text for _, text, _ in batch]

        try:
            # Counted BEFORE the call, not after it. Run 1 recorded
            # api_calls=1 with attempted=0: eight rows were sent to the
            # provider and none of them counted, because the increment
            # sat after a call that raised. A row that was sent has
            # been attempted regardless of what came back.
            counters.attempted += len(batch)
            counters.api_calls += 1
            vectors = await client.embed_documents(texts)

        except EmbeddingQuotaError as exc:
            # Undo the pre-call increment. A 429 means the request was
            # REFUSED, not answered -- the provider never saw these rows, so
            # they belong in `abandoned`, not `attempted`. api_calls still
            # counts the refused call, because the quota counts it. This
            # mirrors the per-row `attempted -= 1` in cv_embedding.py; without
            # it the two modules would mean different things by the same
            # counter.
            counters.attempted -= len(batch)

            # Not retried and not fallen back on. Retrying spends what
            # little is left, and every remaining batch would fail the
            # same way.
            status = EmbeddingStatus.QUOTA_EXCEEDED
            error_message = str(exc)
            logger.error("Embedding stopped: quota exhausted.")
            break

        except EmbeddingError as exc:
            # A batch is all-or-nothing, and the response does not say
            # which input offended. So fall back to one call per row
            # to isolate it -- the same thing embedding_isolate.py does
            # at debug time, done here at run time. Nineteen good rows
            # are not lost to one bad one.
            logger.warning("Batch of %d failed; isolating.", len(batch))
            error_message = str(exc)
            await _embed_individually(client, batch, counters)
            continue

        else:
            async with session_scope() as session:
                repository = JobRepository(session)
                now = datetime.now(timezone.utc)
                for (job_id, _, digest), vector in zip(batch, vectors, strict=True):
                    counters.succeeded += 1
                    await repository.set_embedding(
                        job_id, vector, client.model, digest, now
                    )

    async with session_scope() as session:
        remaining_null = await JobRepository(session).count_active_missing_embedding()

    if status is None:
        status = _classify(counters, total_in_scope)

    aborted = status in (
        EmbeddingStatus.QUOTA_EXCEEDED,
        EmbeddingStatus.PROVIDER_ERROR,
    )

    if not counters.accounted_for():
        # More rows processed than selected, or an attempted count that
        # does not split into successes and failures. Either way a row
        # was lost or double-counted between selection and storage.
        logger.error("Embedding funnel does not balance: %s", asdict(counters))
    elif counters.abandoned_unexpectedly(aborted):
        # Nothing aborted, and rows still went missing.
        logger.error(
            "Embedding left %d candidate(s) unprocessed without aborting.",
            counters.abandoned,
        )
    elif counters.abandoned:
        # Expected and already reported by the status. Informational.
        logger.info(
            "Embedding stopped early with %d candidate(s) unprocessed. "
            "Re-run to continue.",
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


async def _embed_individually(
    client: GeminiEmbeddingClient,
    batch: list[tuple[int, str, str]],
    counters: _Counters,
) -> None:
    """One call per row, to find which row a failed batch was about."""
    for job_id, text, digest in batch:
        # attempted is NOT incremented here. The batch loop already
        # counted every row in this batch when it sent them. Counting
        # again would make attempted exceed candidates_considered and
        # push abandoned negative -- which accounted_for() would then
        # correctly report as a broken funnel.
        try:
            counters.api_calls += 1
            vectors = await client.embed_documents([text])
        except EmbeddingError as exc:
            counters.failed += 1
            async with session_scope() as session:
                await JobRepository(session).mark_embedding_failed(job_id, str(exc))
            continue

        counters.succeeded += 1
        async with session_scope() as session:
            await JobRepository(session).set_embedding(
                job_id, vectors[0], client.model, digest, datetime.now(timezone.utc)
            )
