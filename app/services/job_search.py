"""Find the jobs closest to a vector, and score how close.

The seam Day 8 builds on. Day 8 reads `similarity` off a JobMatch and
folds it into a weighted score alongside skills, location and
experience; this module is responsible for that one number being
meaningful and for nothing else.

The most useful property of what Parts 4 and 5 stored is that matching
a candidate against every job costs NO API call. Both sides are
already vectors in the same table. The CV was embedded once, as a
RETRIEVAL_QUERY; the jobs were embedded once, as RETRIEVAL_DOCUMENTs;
comparing them is arithmetic Postgres does. Only a free-text search
typed by a user needs the provider, because that text has never been
embedded before.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.repositories.cv import CVRepository
from app.db.repositories.job import JobRepository
from app.db.session import session_scope
from app.integrations.gemini_embeddings import GeminiEmbeddingClient

# Above pgvector's default of 40. The default keeps 40 candidates in
# flight while descending the graph, so asking for a top-10 leaves
# little room for the index to discard bad paths. Raising it costs a
# little search time and buys recall.
DEFAULT_EF_SEARCH = 64


@dataclass(frozen=True)
class JobMatch:
    """One job and how close it is. The unit Day 8 consumes."""

    job_id: int
    title: str
    company: str | None
    location: str | None
    distance: float
    similarity: float


def _to_similarity(distance: float) -> float:
    """Cosine distance to a 0-1 score.

    pgvector's `<=>` returns 1 - cosine_similarity, so similarity is
    1 - distance. With unit vectors that lands in [-1, 1] in theory,
    though text embeddings almost never go negative in practice.

    Clamped at 0 anyway. Day 8 weights this into a total, and a
    negative component there would not merely rank a job last -- it
    would subtract from the other signals' contributions and make a
    strong skill match look weaker than no match at all.
    """
    return max(0.0, 1.0 - distance)


def _to_match(job, distance: float) -> JobMatch:
    return JobMatch(
        job_id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        distance=distance,
        similarity=_to_similarity(distance),
    )


async def search_by_vector(
    vector: list[float],
    limit: int = 10,
    ef_search: int = DEFAULT_EF_SEARCH,
) -> list[JobMatch]:
    """Closest active jobs to an already-computed vector. No API call."""
    async with session_scope() as session:
        rows = await JobRepository(session).nearest_to(vector, limit, ef_search)
        return [_to_match(job, distance) for job, distance in rows]


async def search_for_user(
    user_id: int,
    limit: int = 10,
    ef_search: int = DEFAULT_EF_SEARCH,
) -> list[JobMatch] | None:
    """Match a user's stored CV against every stored job. No API call.

    Returns None when the user has no embedded active CV version,
    which is a different thing from an empty result list. Empty means
    "searched, found nothing"; None means "could not search". Day 8
    must not show those two the same way.
    """
    async with session_scope() as session:
        version = await CVRepository(session).active_version_with_embedding(user_id)
        if version is None:
            return None
        vector = list(version.embedding)

    return await search_by_vector(vector, limit, ef_search)


async def search_by_text(
    query_text: str,
    limit: int = 10,
    client: GeminiEmbeddingClient | None = None,
    ef_search: int = DEFAULT_EF_SEARCH,
) -> list[JobMatch]:
    """Match free text against stored jobs. COSTS ONE API CALL.

    Embedded with embed_query(), not embed_documents(). Typed text is
    a query, and the two task types produce measurably different
    vectors -- cosine 0.861247 between them for identical input. Using
    the document task type here would compare a query against jobs
    using the wrong half of that distinction, and nothing would report
    it.
    """
    client = client or GeminiEmbeddingClient()
    vector = await client.embed_query(query_text)
    return await search_by_vector(vector, limit, ef_search)


async def self_check(job_id: int, ef_search: int = DEFAULT_EF_SEARCH) -> dict:
    """Search using a job's own stored vector. No API call.

    An end-to-end proof that costs nothing. A job's own vector must
    return that job first, at similarity 1.0. If it does not, the
    failure is between the vector leaving the provider and coming back
    out of Postgres -- storage, retrieval, the operator, or the
    ordering -- and this separates that from every question about
    whether the embeddings are any good.

    Returns the top job's id and similarity, plus whether they are
    what they must be.
    """
    async with session_scope() as session:
        repository = JobRepository(session)
        job = await repository.by_id(job_id)
        if job is None or job.embedding is None:
            return {"ok": False, "reason": "job missing or not embedded"}

        rows = await repository.nearest_to(list(job.embedding), 1, ef_search)

    if not rows:
        return {"ok": False, "reason": "no results"}

    top, distance = rows[0]
    similarity = _to_similarity(distance)

    return {
        "ok": top.id == job_id and similarity > 0.9999,
        "queried_job_id": job_id,
        "top_job_id": top.id,
        "similarity": similarity,
    }
