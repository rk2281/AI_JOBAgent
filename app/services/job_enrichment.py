"""Turn stored jobs into extracted skills and experience bounds.

Mirrors app/services/job_embedding.py's shape closely and for the
same reasons: this is a separate, idempotent, re-runnable pass rather
than something ingestion does inline, because it is rationed by a
different quota than Adzuna's, and because re-tuning the enrichment
prompt must be able to re-run over every stored job WITHOUT
re-ingesting anything.

Runs are recorded in `embedding_runs` with `scope="job_skills"`. That
table's `scope` column exists for exactly this: its eight
EmbeddingStatus values map one-for-one onto this pass's outcomes
(healthy: COMPLETE / NOTHING_TO_DO; broken: NO_SOURCE_ROWS,
ALL_FAILED, PARTIAL, QUOTA_EXCEEDED, PROVIDER_ERROR), and its funnel
columns -- candidates_considered, skipped_empty_text, attempted,
succeeded, failed, api_calls, remaining_null -- fit this pass exactly
as written, with no new columns needed. The cost of reusing it is that
`embedding_runs` now holds runs which are not embeddings at all.
Anything later asking "did embedding work?" MUST filter on
`scope = 'jobs'` or it will read scoring rows mixed in with vector
rows and draw a conclusion about the wrong pass.

One job, one API call, never batched -- see
app/integrations/gemini_enrichment.py for why batching generation
calls is unsafe in a way batching embeddings is not. That also means
this pass commits PER JOB rather than per batch. Proven live on Day 7
in the embedding pass: run 2 kept the 72 rows it had already written
when it stopped, and run 3 only had to finish the remaining 27. A
pass that batches its commits loses everything it paid for the moment
it is interrupted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic

from app.core.config import settings
from app.db.models.embedding import EmbeddingStatus
from app.db.repositories.job import EmbeddingRunRepository, JobRepository
from app.db.repositories.skill import SkillRepository
from app.db.session import session_scope
from app.integrations.gemini_embeddings import describe_genai_error, is_quota_error
from app.integrations.gemini_enrichment import (
    EnrichmentError,
    EnrichmentIdMismatch,
    EnrichmentProviderError,
    GeminiEnrichmentClient,
)
from app.services.embedding_text import build_job_document, document_hash
from app.services.job_enrichment_rules import filter_and_normalize_skills, infer_work_mode

logger = logging.getLogger(__name__)

SCOPE_JOB_SKILLS = "job_skills"


def _is_quota_exceeded(error: Exception | None) -> bool:
    """Whether a provider exception means the enrichment quota is spent.

    Checked structurally, never by matching message text -- message
    text is exactly what describe_genai_error() now refuses to carry,
    and a substring match over it would also be unreliable in the
    normal sense: it would fire on a quota-shaped phrase appearing
    anywhere in an echoed request.

    Two independent checks, either one sufficient:

      - the exception's own class name contains "RateLimit". A live
        429 surfaced here as a RateLimitError whose numeric attributes
        did not reliably compare equal to the int 429 that
        is_quota_error() (gemini_embeddings.py) checks -- the class
        name turned out to be the more durable signal.
      - an HTTP status of 429, read tolerantly from `.code` or
        `.status_code` and coerced to int rather than compared by
        identity, since that live error's status was not reliably
        typed as a plain int either.

    is_quota_error() is still consulted as a third, OR'd check, so the
    RESOURCE_EXHAUSTED-by-status-string case it already covers is not
    lost by adding this.
    """
    if error is None:
        return False

    if "RateLimit" in type(error).__name__:
        return True

    for attribute in ("code", "status_code"):
        value = getattr(error, attribute, None)
        if value is None:
            continue
        try:
            if int(value) == 429:
                return True
        except (TypeError, ValueError):
            continue

    return is_quota_error(error)

# Matches run_job_embedding's own default. The table holds 99 rows
# today; a generous cap here means a plain `python -m
# scripts.enrich_jobs` with no --limit still covers everything.
_DEFAULT_LIMIT = 1000


@dataclass
class _Counters:
    """A funnel, not a summary. See job_embedding._Counters for the
    same idea applied to batches instead of single jobs.

    There is no `abandoned` counter here, unlike the embedding pass.
    A batch of eight can be interrupted mid-batch with some rows
    genuinely in flight; a single job cannot. So on the one abort path
    this pass has -- a quota error -- the job that triggered it is
    backed out of candidates_considered entirely (see run_enrichment),
    and every row after it in the candidate list is simply never
    visited. Nothing is ever "considered but not accounted for", which
    is what keeps the two assertions below unconditional rather than
    needing the aborted-run carve-out job_embedding.py needs.
    """

    candidates_considered: int = 0
    skipped_empty_text: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    api_calls: int = 0
    id_mismatches: int = 0

    def accounted_for(self) -> bool:
        return (
            self.candidates_considered == self.skipped_empty_text + self.attempted
            and self.attempted == self.succeeded + self.failed
        )


def _classify(counters: _Counters, total_in_scope: int) -> EmbeddingStatus:
    """Which kind of "nothing" a zero-progress run actually was.

    Identical reasoning to job_embedding._classify, reused rather than
    duplicated in spirit because it is the same question asked of a
    different funnel: NO_SOURCE_ROWS (broken -- the table itself is
    empty) must never read the same as NOTHING_TO_DO (healthy --
    every active job already has skills).
    """
    if counters.candidates_considered == 0:
        return (
            EmbeddingStatus.NO_SOURCE_ROWS
            if total_in_scope == 0
            else EmbeddingStatus.NOTHING_TO_DO
        )

    if counters.attempted == 0 or counters.succeeded == 0:
        return EmbeddingStatus.ALL_FAILED

    if counters.failed > 0:
        return EmbeddingStatus.PARTIAL

    return EmbeddingStatus.COMPLETE


async def run_enrichment(
    *,
    limit: int | None = None,
    retry_failed: bool = False,
    dry_run: bool = False,
    client: GeminiEnrichmentClient | None = None,
) -> dict:
    """Extract skills and experience bounds for every job that needs it.

    `client` is injectable so tests can pass a stub, exactly like
    run_job_embedding. `limit=None` means the default cap of 1000,
    which is generous against a 99-row table.

    dry_run counts and prints each candidate job (empty-document rows
    included, so the empty-vs-would-attempt split is visible) without
    making a single API call, and returns before any run row is
    written to embedding_runs -- there is nothing to write a status
    for, because nothing ran. scripts/enrich_jobs.py uses the
    candidates_considered / skipped_empty_text this returns to compute
    the printed call budget and time range.

    On a quota error: the run STOPS. It is not retried and no
    fallback is attempted -- the quota will still be exhausted on the
    next job, and every remaining job would fail identically. The job
    that triggered it is backed out of candidates_considered and
    attempted (its own API call never produced a usable answer), and
    the loop simply never reaches the jobs after it, so the funnel
    stays balanced without needing a separate "abandoned" bucket.

    On EnrichmentIdMismatch: this is the only failure here that would
    otherwise be completely silent. The response parses cleanly and
    validates cleanly against the schema -- it would attach one job's
    skills to a different job's row, with nothing downstream ever
    noticing. It is recorded via mark_enrichment_failed like any other
    failure, but also counted separately as `id_mismatches` in the
    returned dict, because "the schema rejected this" and "the model
    answered a different job" are different problems with different
    fixes.

    On any other provider error: mark_enrichment_failed and continue
    to the next job. A single bad job must not stop 98 good ones.

    remaining_null is measured AFTER the pass, via
    count_active_missing_skills(), not derived from the funnel. A run
    can report succeeded=40 and still leave 59 jobs abstaining on 30%
    of the matching model -- the funnel alone would call that a
    success.

    The two assertions below are checked, not logged, because if this
    fires the model is the first suspect, not the data. On Day 7 the
    equivalent embedding assertion fired twice and both times the
    fault was that the model had no name for a state the run had
    actually reached, not that a row had gone missing.
    """
    client = client or GeminiEnrichmentClient()
    effective_limit = limit if limit is not None else _DEFAULT_LIMIT

    async with session_scope() as session:
        repository = JobRepository(session)
        total_in_scope = await repository.count_active_jobs()
        jobs = await repository.list_needing_enrichment(effective_limit, retry_failed)

    counters = _Counters()

    if dry_run:
        for job in jobs:
            document = build_job_document(job.title, job.description)
            counters.candidates_considered += 1
            if not document.strip():
                counters.skipped_empty_text += 1
                print(f"job {job.id}: empty document, would skip")
                continue
            print(f"job {job.id}: {len(document)} chars, would enrich")

        return {
            "status": "dry_run",
            "total_in_scope": total_in_scope,
            "candidates_considered": counters.candidates_considered,
            "skipped_empty_text": counters.skipped_empty_text,
            "would_attempt": counters.candidates_considered - counters.skipped_empty_text,
        }

    started_at = datetime.now(timezone.utc)
    async with session_scope() as session:
        run = await EmbeddingRunRepository(session).start(
            SCOPE_JOB_SKILLS, client.model, started_at
        )
        run_id = run.id

    status: EmbeddingStatus | None = None
    error_message: str | None = None
    call_seconds: list[float] = []
    total_skills_written = 0
    total_dropped_too_long = 0
    total_dropped_soft = 0
    work_mode_remote = 0
    work_mode_hybrid = 0
    work_mode_none = 0

    for job in jobs:
        document = build_job_document(job.title, job.description)

        if not document.strip():
            # Our data gap, not the provider's failure. Counted
            # separately so "we had nothing to send" never reads as
            # "the provider refused".
            counters.candidates_considered += 1
            counters.skipped_empty_text += 1
            continue

        # Counted BEFORE the call, matching run_job_embedding's own
        # rule: a row that was sent has been attempted regardless of
        # what comes back. Both are undone together below if the
        # provider refuses the request outright.
        counters.candidates_considered += 1
        counters.attempted += 1
        counters.api_calls += 1

        call_started = monotonic()
        try:
            enrichment = await client.enrich_job(job.id, document)
        except EnrichmentIdMismatch as exc:
            elapsed = monotonic() - call_started
            call_seconds.append(elapsed)
            logger.info("job %s: %.3fs (id mismatch)", job.id, elapsed)

            counters.failed += 1
            counters.id_mismatches += 1
            error_text = f"job_id mismatch: requested {exc.requested_job_id}, got {exc.returned_job_id}"
            async with session_scope() as session:
                await JobRepository(session).mark_enrichment_failed(job.id, error_text)

        except EnrichmentError as exc:
            elapsed = monotonic() - call_started
            call_seconds.append(elapsed)

            if isinstance(exc, EnrichmentProviderError) and _is_quota_exceeded(exc.__cause__):
                # Refused, not answered. api_calls still counts it --
                # the quota counts it -- but attempted and
                # candidates_considered are undone, because this job
                # was never actually processed.
                #
                # mark_enrichment_failed is deliberately NOT called
                # here. A quota failure says nothing about this row --
                # the exact same job would succeed the moment the
                # quota resets -- and incrementing
                # skills_extraction_attempts for it would push an
                # innocent job toward enrichment_max_attempts and lock
                # it out of future runs for a failure that was never
                # its own. The attempts ceiling exists to stop rows
                # that are genuinely unextractable; letting the quota
                # spend it turns a protection into a hazard.
                counters.attempted -= 1
                counters.candidates_considered -= 1
                status = EmbeddingStatus.QUOTA_EXCEEDED
                error_message = describe_genai_error(exc.__cause__)
                logger.error("Enrichment stopped: quota exhausted.")
                break

            logger.info("job %s: %.3fs (failed)", job.id, elapsed)
            counters.failed += 1
            safe_error = (
                describe_genai_error(exc.__cause__) if exc.__cause__ is not None else str(exc)
            )
            async with session_scope() as session:
                await JobRepository(session).mark_enrichment_failed(job.id, safe_error)

        else:
            elapsed = monotonic() - call_started
            call_seconds.append(elapsed)
            logger.info("job %s: %.3fs (ok)", job.id, elapsed)

            filtered = filter_and_normalize_skills(enrichment.skills)
            # Every dropped entry is counted, not just discarded. A
            # filter that silently removes a quarter of a job's skills
            # is the same invisible-versus-wrong failure this project
            # keeps paying for -- see job_enrichment_rules.py.
            total_dropped_too_long += len(filtered.dropped_too_long)
            total_dropped_soft += len(filtered.dropped_soft)

            work_mode = infer_work_mode(job.title, job.location, job.description)
            if work_mode == "remote":
                work_mode_remote += 1
            elif work_mode == "hybrid":
                work_mode_hybrid += 1
            else:
                work_mode_none += 1

            async with session_scope() as session:
                skill_repository = SkillRepository(session)
                skill_ids = [
                    (await skill_repository.get_or_create(name)).id
                    for name in filtered.kept
                ]

                job_repository = JobRepository(session)
                written = await job_repository.replace_job_skills(job.id, skill_ids)
                total_skills_written += written

                await job_repository.set_enrichment(
                    job.id,
                    model=client.model,
                    source_hash=document_hash(document),
                    min_experience_years=enrichment.min_experience_years,
                    max_experience_years=enrichment.max_experience_years,
                    work_mode=work_mode,
                    extracted_at=datetime.now(timezone.utc),
                )

            counters.succeeded += 1

        if settings.enrichment_seconds_between_calls > 0:
            await asyncio.sleep(settings.enrichment_seconds_between_calls)

    async with session_scope() as session:
        remaining_null = await JobRepository(session).count_active_missing_skills()

    assert counters.candidates_considered == counters.skipped_empty_text + counters.attempted, (
        f"enrichment funnel does not balance: considered={counters.candidates_considered} "
        f"skipped={counters.skipped_empty_text} attempted={counters.attempted}"
    )
    assert counters.attempted == counters.succeeded + counters.failed, (
        f"enrichment funnel does not balance: attempted={counters.attempted} "
        f"succeeded={counters.succeeded} failed={counters.failed}"
    )

    if status is None:
        status = _classify(counters, total_in_scope)

    async with session_scope() as session:
        await EmbeddingRunRepository(session).finish(
            run_id=run_id,
            status=status.value,
            finished_at=datetime.now(timezone.utc),
            counters={
                "candidates_considered": counters.candidates_considered,
                "skipped_empty_text": counters.skipped_empty_text,
                "attempted": counters.attempted,
                "succeeded": counters.succeeded,
                "failed": counters.failed,
                "api_calls": counters.api_calls,
            },
            remaining_null=remaining_null,
            error_message=error_message,
        )

    return {
        "status": status.value,
        "total_in_scope": total_in_scope,
        "candidates_considered": counters.candidates_considered,
        "skipped_empty_text": counters.skipped_empty_text,
        "attempted": counters.attempted,
        "succeeded": counters.succeeded,
        "failed": counters.failed,
        "id_mismatches": counters.id_mismatches,
        "api_calls": counters.api_calls,
        "remaining_null": remaining_null,
        "total_skills_written": total_skills_written,
        "total_dropped_too_long": total_dropped_too_long,
        "total_dropped_soft": total_dropped_soft,
        "work_mode_remote": work_mode_remote,
        "work_mode_hybrid": work_mode_hybrid,
        "work_mode_none": work_mode_none,
        "call_seconds": call_seconds,
        "error_message": error_message,
    }
