"""Gemini client: text in, a validated unit vector out.

A sibling of gemini.py rather than an addition to it. That file is the
CV-extraction client and says so in its own docstring; the two share a
provider and nothing else, and a module that does both would have to
explain twice which half a reader is looking at.

Everything below is shaped by what `python -m scripts.embedding_isolate`
measured against the live API rather than by documentation:

  - gemini-embedding-001 returns 3072 dimensions by default and
    accepts output_dimensionality=768. The 768-dimension output has an
    L2 norm of 0.585560, NOT 1.0 -- a Matryoshka truncation is not
    re-normalised by the provider. So this module normalises before
    returning. Cosine distance would tolerate the raw vector, but a
    table where every row has norm 1.0 makes a corrupt row findable
    with one query, and a table of assorted norms makes it findable
    with none.

  - task_type is genuinely applied by this model. The same text as
    RETRIEVAL_DOCUMENT and as RETRIEVAL_QUERY comes back with cosine
    0.861247. On gemini-embedding-2 the same comparison gives
    1.000000, meaning ignored, which is why that model is not used.

  - A batch of eight returns eight vectors in input order, verified by
    embedding [X, Y, X] and confirming vectors 0 and 2 are identical.
    gemini-embedding-2 returned ONE vector for the same eight inputs
    -- not an error, one vector -- which is why _validate_vectors
    checks the count before anything is zipped back onto a row.
"""

from __future__ import annotations

import math
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings

# Same policy as app/integrations/gemini.py, and for the same reason:
# retrying a 429 with backoff turns an exhausted quota into a client
# timeout, which sends the next debugging session after the transport
# instead of the quota.
_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=3,
    http_status_codes=[408, 500, 502, 503, 504],
)

# Shorter than the 120s used for extraction. An embedding call is one
# forward pass over a short document; the measured times were 0.5-1.5
# seconds. A minute is generous, and a deadline that generous still
# separates "slow" from "hung".
DEFAULT_TIMEOUT_SECONDS = 60.0


class EmbeddingError(Exception):
    """Base for anything that stopped text from becoming a stored vector."""


class EmbeddingQuotaError(EmbeddingError):
    """The quota is spent. Not transient; retrying spends what is left."""


class EmbeddingProviderError(EmbeddingError):
    """The provider could not be reached, or refused the request."""


class EmbeddingResponseError(EmbeddingError):
    """The provider answered, and the answer cannot be stored.

    Separate from EmbeddingProviderError because the response differs.
    A provider error means try again later; this means the response
    was the wrong shape, wrong dimension, or a zero vector, and trying
    again will produce the same thing.
    """


def describe_genai_error(error: Exception) -> str:
    """Describe a provider error without formatting the error object.

    Reads only `code` and `message`. Mirrors _format_errors() in
    gemini.py and describe_http_error() in http_errors.py, and exists
    for the same reason: an exception's string form can carry the
    request that produced it, and on this path the request is job text
    or a candidate's CV. An error is not safe to format just because
    it is an error.

    scripts/embedding_isolate.py carries an identical copy on purpose.
    That script has to run before this module exists to be trusted,
    which is the whole point of it, so it cannot import from here.
    """
    parts = [type(error).__name__]

    code = getattr(error, "code", None)
    if code is not None:
        parts.append(f"code={code}")

    message = getattr(error, "message", None)
    if isinstance(message, str) and message:
        parts.append(message)

    return " | ".join(parts)


def is_quota_error(error: Exception) -> bool:
    """Whether this failure means the quota is spent.

    Checked on attributes only, never by searching str(error). A
    substring search over an exception's string form is both a leak
    risk and unreliable -- it would match a quota-shaped word appearing
    anywhere in an echoed request.
    """
    if getattr(error, "code", None) == 429:
        return True

    status = getattr(error, "status", None)
    return isinstance(status, str) and status.upper() == "RESOURCE_EXHAUSTED"


def normalize(vector: list[float]) -> list[float]:
    """Scale to unit length.

    Raises rather than returning a zero vector. A zero vector is the
    single worst thing that can reach the embedding column: it is
    equidistant from every other vector, so the row it belongs to
    appears at an essentially arbitrary position in every ranking,
    while the column itself looks perfectly populated. Nothing
    downstream would ever report it.
    """
    norm = math.sqrt(sum(component * component for component in vector))

    # Exact zero. A small-but-nonzero norm is legitimate -- the
    # measured 768-dimension output has norm 0.5856 -- and testing
    # with < some threshold would reject healthy vectors.
    if norm == 0.0:
        raise EmbeddingResponseError("provider returned a zero vector")

    return [component / norm for component in vector]


def _extract_vectors(response: Any) -> list[list[float]]:
    """Pull plain float lists out of the SDK's response object."""
    embeddings = getattr(response, "embeddings", None) or []

    vectors: list[list[float]] = []
    for item in embeddings:
        values = getattr(item, "values", None)
        if values is None:
            continue
        vectors.append([float(value) for value in values])

    return vectors


def _validate_vectors(
    vectors: list[list[float]],
    *,
    expected_count: int,
    expected_dimension: int,
) -> None:
    """Refuse anything that cannot be safely zipped back onto its rows.

    The count check is not defensive padding. gemini-embedding-2
    returned one vector for a batch of eight inputs, and code that
    trusted the response would have attached that single vector to the
    first job and silently dropped the other seven.
    """
    if len(vectors) != expected_count:
        raise EmbeddingResponseError(
            f"expected {expected_count} vector(s), received {len(vectors)}"
        )

    for index, vector in enumerate(vectors):
        if len(vector) != expected_dimension:
            raise EmbeddingResponseError(
                f"vector {index} has dimension {len(vector)}, "
                f"expected {expected_dimension}"
            )


class GeminiEmbeddingClient:
    """Wraps one Gemini embedding model at one fixed dimension."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Public: the service writes this into embedding_model on every
        # row it fills, so it has to be readable back.
        self.model = model or settings.gemini_embedding_model
        self.dimension = dimension or settings.embedding_dimension
        self.timeout_seconds = timeout_seconds

        self._client = genai.Client(
            api_key=api_key or settings.gemini_api_key,
            http_options=types.HttpOptions(
                retry_options=_RETRY_OPTIONS,
                # Milliseconds. Confirmed against the installed
                # package in Day 4.5; see gemini.py for the detail.
                timeout=int(timeout_seconds * 1000),
            ),
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed job text. One vector per input, in input order."""
        return await self._embed(texts, settings.embedding_task_type_document)

    async def embed_query(self, text: str) -> list[float]:
        """Embed CV text -- the side doing the searching."""
        vectors = await self._embed([text], settings.embedding_task_type_query)
        return vectors[0]

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if not texts:
            # No call, so no quota spent. An empty batch is a caller
            # bookkeeping question, not a request.
            return []

        if any(not text.strip() for text in texts):
            # A programming error rather than a provider one: the
            # service is responsible for counting empty documents as
            # skipped before it gets here.
            raise ValueError("refusing to embed an empty document")

        try:
            response = await self._client.aio.models.embed_content(
                model=self.model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.dimension,
                ),
            )
        except Exception as error:  # noqa: BLE001 - provider errors are not typed
            if is_quota_error(error):
                raise EmbeddingQuotaError(describe_genai_error(error)) from error
            raise EmbeddingProviderError(describe_genai_error(error)) from error

        vectors = _extract_vectors(response)
        _validate_vectors(
            vectors,
            expected_count=len(texts),
            expected_dimension=self.dimension,
        )

        return [normalize(vector) for vector in vectors]
