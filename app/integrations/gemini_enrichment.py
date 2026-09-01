"""Gemini client: one job's text in, its structured skills and
experience bounds out.

One job per call, never batched, on purpose. Day 7's
gemini-embedding-2 returned ONE vector for a batch of eight inputs --
not an error, one vector -- and that was catchable because a returned
count can be compared against an input count. The generation
equivalent is not catchable the same way: a model that returns six
objects for eight jobs, or the same count in a different order,
produces JSON that parses cleanly and validates cleanly, and attaches
job 3's skills to job 5's row. No count check, no dimension check,
nothing downstream would ever notice -- the damage would show up only
as rankings that feel slightly wrong.

The only defence against that is `job_id` travelling in the prompt and
coming back in the response. `enrich_job()` compares it against the
id it was asked for before returning anything to the caller, and
raises rather than returning a result that might belong to a
different job.

ENRICHMENT_SCHEMA and ENRICHMENT_PROMPT live here, not in
scripts/enrichment_isolate.py, so that script imports them instead of
duplicating them. What was proven against four live isolate runs is
then exactly what runs against the 99 stored jobs -- a copy that
drifted from the proven one would make everything measured there
meaningless.

Every error path goes through describe_genai_error(), never
str(exc). A google-genai error can echo back the request that
produced it, and on this path the request is job text.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from google import genai
from google.genai import types

from app.core.config import settings
from app.integrations.gemini_embeddings import describe_genai_error

# Same list, same reasoning, as gemini.py and gemini_embeddings.py:
# a 429 means the quota is spent, and retrying it spends what little
# is left instead of surfacing the exhaustion. Server errors are
# genuinely transient and stay in the list; 429 is not.
#
# Observed live without this: three jobs, four SDK retries each --
# twelve calls burned against a quota that was already exhausted,
# because this client was constructed without a retry_options at all
# and fell back to the SDK's default policy, which does retry 429.
_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=3,
    http_status_codes=[408, 500, 502, 503, 504],
)

ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "integer"},
        "skills": {
            "type": "array",
            "items": {"type": "string"},
        },
        "min_experience_years": {"type": ["integer", "null"]},
        "max_experience_years": {"type": ["integer", "null"]},
    },
    "required": [
        "job_id",
        "skills",
        "min_experience_years",
        "max_experience_years",
    ],
}

ENRICHMENT_PROMPT = (
    "Read the job posting below and return JSON only.\n\n"
    "skills: the technologies, tools, languages, frameworks, "
    "platforms and named methodologies the posting asks for. Use the "
    "posting's own wording.\n\n"
    "Each entry must be a NAME, not a description of an activity and "
    "not a personal quality. 'PyTorch' is a skill. 'deploying models "
    "to production' is an activity and must not be listed. 'Strong "
    "communication skills' is a personal quality and must not be "
    "listed. Prefer one or two words; never more than three.\n\n"
    "Return an empty list if the posting names none. Do not infer "
    "skills that are not written down.\n\n"
    "min_experience_years and max_experience_years: the years of "
    "experience the posting requires. Return null for either bound "
    "the posting does not state. Do NOT return 0 to mean 'not "
    "stated' -- 0 means the posting explicitly welcomes candidates "
    "with no experience, and null means the posting is silent. These "
    "are different and must not be confused.\n\n"
    "job_id: echo back the job_id given below, unchanged.\n"
)

_SCHEMA_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": ENRICHMENT_SCHEMA,
}


@dataclass(frozen=True)
class JobEnrichment:
    """One job's extracted skills and experience bounds.

    Frozen for the same reason JobMatch is frozen: a value that can be
    edited between the provider and the database is one nobody can
    trace back to what Gemini actually returned.

    min_experience_years and max_experience_years being None means
    the posting was SILENT about experience -- it never mentioned a
    requirement. That is different from 0, which means the posting
    explicitly welcomes candidates with no experience. The same
    distinction profiles.total_experience_years carries on the
    candidate side, and conflating the two here would make every
    silent posting look like it welcomes freshers.
    """

    job_id: int
    skills: list[str]
    min_experience_years: int | None
    max_experience_years: int | None


class EnrichmentError(Exception):
    """Base for anything that stopped job text from becoming a JobEnrichment."""


class EnrichmentIdMismatch(EnrichmentError):
    """The model echoed back a job_id that is not the one requested.

    The message carries both ids and nothing else -- no job text, no
    response body. That is the only thing safe to store or log on
    this path.
    """

    def __init__(self, requested_job_id: int, returned_job_id: int) -> None:
        super().__init__(
            f"requested job_id={requested_job_id}, "
            f"model returned job_id={returned_job_id}"
        )
        self.requested_job_id = requested_job_id
        self.returned_job_id = returned_job_id


class EnrichmentProviderError(EnrichmentError):
    """The provider could not be reached, or refused the request."""


class EnrichmentResponseError(EnrichmentError):
    """The provider answered, but the answer cannot be used.

    Separate from EnrichmentProviderError because the response
    differs: a provider error means try again later, this means the
    response was not valid JSON or did not fit the schema, and trying
    again will very likely produce the same thing.
    """


def _coerce_skills(raw: object) -> list[str]:
    """Keep only non-blank strings. Word-count filtering is a matching
    rule and belongs in the service, where it can be counted."""
    if not isinstance(raw, list):
        return []

    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def _coerce_experience_bound(raw: object) -> int | None:
    """Any non-integer, including a bool, is treated as None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    return None


class GeminiEnrichmentClient:
    """Wraps one Gemini model configured for per-job skill extraction."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.model = model or settings.gemini_model
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.enrichment_timeout_seconds
        )
        self._client = genai.Client(
            api_key=api_key or settings.gemini_api_key,
            http_options=types.HttpOptions(
                retry_options=_RETRY_OPTIONS,
                timeout=int(self.timeout_seconds * 1000),
            ),
        )

    async def enrich_job(self, job_id: int, document: str) -> JobEnrichment:
        """Send one job's document to Gemini and return its JobEnrichment.

        `document` is the output of build_job_document(title,
        description) -- the caller passes it in rather than this
        client building it, so the text rule stays in
        app/services/embedding_text.py, the same place the embedding
        pass already reads it from. Both then look at the same job.

        Raises EnrichmentIdMismatch if the model echoes back a
        different job_id than requested. Raises EnrichmentProviderError
        or EnrichmentResponseError on any other failure. Never returns
        a result that might belong to a different job.
        """
        prompt = ENRICHMENT_PROMPT + f"\njob_id: {job_id}\n\n--- JOB POSTING ---\n{document}\n"

        try:
            interaction = await self._client.aio.interactions.create(
                model=self.model,
                input=prompt,
                response_format=_SCHEMA_FORMAT,
            )
        except Exception as error:  # noqa: BLE001 - provider errors are not typed here
            raise EnrichmentProviderError(describe_genai_error(error)) from error

        if interaction.status != "completed":
            raise EnrichmentResponseError(
                f"Gemini returned status={interaction.status!r}"
            )

        text = interaction.output_text
        if text is None:
            raise EnrichmentResponseError(
                "Gemini reported status='completed' but returned no output_text"
            )

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise EnrichmentResponseError(
                f"Gemini response was not valid JSON: {error}"
            ) from error

        returned_job_id = parsed.get("job_id")
        if returned_job_id != job_id:
            raise EnrichmentIdMismatch(job_id, returned_job_id)

        return JobEnrichment(
            job_id=job_id,
            skills=_coerce_skills(parsed.get("skills")),
            min_experience_years=_coerce_experience_bound(
                parsed.get("min_experience_years")
            ),
            max_experience_years=_coerce_experience_bound(
                parsed.get("max_experience_years")
            ),
        )
