"""Gemini client: raw CV text in, a validated CVProfile out.

The only file in the codebase that imports google.genai. Everything
above this — the extraction service, the bot, scripts/extract_cv.py —
talks to GeminiClient's typed interface and knows nothing about the
provider's SDK shape. Swapping providers, or adding a second one for
comparison, touches only this file plus one new sibling in
app.integrations.

The response_format below asks the API to constrain its own output to
CVProfile's JSON schema — the model is not merely instructed to
produce that shape, it is restricted to it. Validation with
CVProfile.model_validate_json() still happens afterward regardless:
constrained decoding narrows what comes back, it does not guarantee
the values inside are sensible, and treating an external API's output
as trusted without validating it at the boundary is exactly the kind
of mistake this layer exists to prevent.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.cv_profile import CVProfile

# Retry 5xx and 408, but NOT 429. This is a diagnostic decision before
# it is a behavioural one. The SDK's default policy retries 429 with
# exponential backoff, so an exhausted daily quota — which will still
# be exhausted in ninety seconds — consumed the entire request timeout
# in backoff and then surfaced as "Request timed out. This is a
# client-side timeout." That message sent a whole debugging session
# after the async transport, the CV size, and the model, when the real
# answer was a 429 the client had swallowed on our behalf. A quota
# error is not transient and retrying it only hides it.
#
# Server errors are genuinely transient and stay in the list.
_RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=3,
    http_status_codes=[408, 500, 502, 503, 504],
)

# Generous rather than tight. The point of a timeout here is to stop a
# hung request holding resources indefinitely, not to police how long a
# healthy call may take — a long CV legitimately takes tens of seconds,
# and a deadline trimmed close to the average turns ordinary slowness
# into a failed extraction.
DEFAULT_TIMEOUT_SECONDS = 120.0

EXTRACTION_PROMPT = """\
You are extracting structured data from a candidate's CV. Read the \
raw text below and return only the facts it actually contains.

Do not invent employers, dates, titles, or skills that are not \
present in the text. If a field is not present in the CV, leave it \
empty or null rather than guessing a plausible-looking value.

For each role, copy the dates as written into start_date and \
end_date, and ALSO fill start_year, start_month, end_year and \
end_month as numbers whenever the text makes them clear. A CV \
reading "Jan 2022 - Mar 2024" gives start_year 2022, start_month 1, \
end_year 2024, end_month 3. A CV reading only "2022 - 2024" gives \
the years and leaves the months null. Set is_current true only where \
the CV says the role is ongoing, for example "Present" or "Current"; \
leave end_year and end_month null in that case.

Do not fill total_experience_years. It is calculated from the fields \
above.

For target_roles, list the job titles this candidate appears to be \
aiming at, taken from a stated objective, a headline, or the \
direction of their recent work. Leave it empty if the CV gives no \
indication.

--- CV TEXT ---
{text}
"""


class GeminiExtractionError(Exception):
    """Gemini could not be reached, or returned something that does not fit CVProfile."""


def _build_response_schema() -> dict[str, Any]:
    """Turn CVProfile's JSON schema into one the model actually obeys.

    CVProfile.model_json_schema() is valid JSON Schema but a weak
    instruction, in two specific ways that were measured against a
    live call, not guessed at:

    1. No "required" list. Every CVProfile field carries a default, so
       Pydantic marks nothing required, and the model reads that as
       permission to omit whatever is expensive to produce. Against a
       real CV containing three jobs, a degree and forty-odd skills,
       it returned summary and current_title and dropped all three
       array fields.

    2. Nested models are factored into $defs and referenced by $ref. A
       provider that does not resolve references sees an array of
       unconstrained objects, which is no constraint at all.

    Both are fixed here, on the schema, rather than on CVProfile.
    Dropping the model's defaults would make required fields required
    in Python too, so a partial answer would raise ValidationError and
    become a total extraction failure — strictly worse than storing
    what did come back. Asking firmly and still tolerating an
    incomplete reply are different jobs, and they belong in different
    places.
    """
    schema = copy.deepcopy(CVProfile.model_json_schema())
    definitions = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return resolve(copy.deepcopy(definitions[reference.split("/")[-1]]))

            resolved = {key: resolve(value) for key, value in node.items()}
            if resolved.get("type") == "object" and "properties" in resolved:
                resolved["required"] = sorted(resolved["properties"])
            return resolved

        if isinstance(node, list):
            return [resolve(item) for item in node]

        return node

    return resolve(schema)


def _format_errors(errors: Iterable[Any] | None) -> str:
    """Render a provider error list into one line safe to store and log.

    Only code and message are read — the two fields an Error carries.
    The Interaction object also echoes back the request's input, which
    is the entire CV text; anything that formatted the object wholesale
    would put a candidate's personal data into extraction_error and
    every log sink downstream of it.
    """
    if not errors:
        return "no error detail supplied"

    return "; ".join(
        f"{getattr(error, 'code', None)}: {getattr(error, 'message', None)}"
        for error in errors
    )


class GeminiClient:
    """Wraps one Gemini model configured for CV profile extraction."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Public: app.services.cv_extraction records which model
        # produced a given CVVersion, so it needs to read this back.
        self.model = model or settings.gemini_model
        self.timeout_seconds = timeout_seconds
        self._client = genai.Client(
            api_key=api_key or settings.gemini_api_key,
            http_options=types.HttpOptions(
                retry_options=_RETRY_OPTIONS,
                # HttpOptions.timeout is in MILLISECONDS (confirmed
                # against the installed google-genai package:
                # HttpOptions.timeout's docstring says so, and
                # _api_client.get_timeout_in_seconds() divides it by
                # 1000.0 before handing it to httpx). This is also the
                # only place the SDK reads a timeout from — the
                # timeout= kwarg on interactions.create() is silently
                # ignored, so do not re-add it there.
                timeout=int(timeout_seconds * 1000),
            ),
        )

    async def extract_profile(self, raw_text: str) -> CVProfile:
        """Send CV text to Gemini and return a validated CVProfile.

        Raises GeminiExtractionError on any failure — a network error,
        a timeout, an API error, or a response that does not validate
        as a CVProfile. Callers decide what an extraction failure means
        for their caller (the orchestration function in
        app.services.cv_extraction marks the CV FAILED); this method's
        only job is to never return something half-trustworthy.
        """
        try:
            # The SDK's async surface, not asyncio.to_thread. This is
            # the only place a timeout can honestly be enforced.
            # asyncio.wait_for around a to_thread call abandons the
            # thread without cancelling it — the HTTP request keeps
            # running, and whatever it was holding stays held.
            #
            # No timeout= kwarg here: interactions.create() silently
            # ignores it. The timeout is set once, in __init__, via
            # types.HttpOptions(timeout=...) at client construction —
            # that is the only place the SDK actually reads it from.
            interaction = await self._client.aio.interactions.create(
                model=self.model,
                input=EXTRACTION_PROMPT.format(text=raw_text),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _build_response_schema(),
                },
            )
        except Exception as error:  # noqa: BLE001 - provider errors are not typed here
            raise GeminiExtractionError(
                f"Gemini request failed: {error}"
            ) from error

        # A server-side failure does not raise. It comes back as a
        # well-formed Interaction with status='failed', the reason in
        # .errors, and output_text=None. Handing that None straight to
        # model_validate_json() does still end as a GeminiExtractionError
        # — but one reading "JSON input should be string, bytes or
        # bytearray", which points at our schema when the actual cause
        # was the provider's, and is discarded with the object. Checking
        # status first is the difference between a diagnosable failure
        # and a misleading one.
        if interaction.status != "completed":
            raise GeminiExtractionError(
                f"Gemini returned status={interaction.status!r}: "
                f"{_format_errors(interaction.errors)}"
            )

        # Distinct from the above: the provider considers the request
        # finished and successful, yet produced no text. Not expected,
        # but conflating it with a validation failure would send the
        # next reader looking at CVProfile for a problem that is not
        # there.
        if interaction.output_text is None:
            raise GeminiExtractionError(
                "Gemini reported status='completed' but returned no output_text"
            )

        try:
            return CVProfile.model_validate_json(interaction.output_text)
        except ValidationError as error:
            raise GeminiExtractionError(
                f"Gemini response did not match CVProfile: {error}"
            ) from error