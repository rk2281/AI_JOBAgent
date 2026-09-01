"""Three escalating Gemini calls, timed, to find where enrichment would hang.

Same reasoning as scripts/gemini_isolate.py, applied to a different
call: extraction hung for the full timeout once already, on this same
provider, and elapsed time is what tells a hang from a rejection. A
call that fails at 2s was refused; one that fails at 45s was hung, and
they point in opposite directions.

Each stage isolates one variable:

  A. tiny input, NO response schema.
     Answers: does this account reach settings.gemini_model at all
     today? If A fails, nothing about the schema is involved.

  B. a short, ordinary job description, WITH the four-field
     enrichment schema.
     Answers: does the schema itself hang or get rejected? This is
     the stage that matters most, for the same reason Day 5 lost
     three hours to CV extraction: a schema the service dislikes can
     leave a request pending rather than rejecting it outright.

  C. a description that names technologies but states NO years of
     experience anywhere, WITH the schema.
     Answers: does the model return null for the experience bounds,
     or does it invent 0? Both are valid integers under this schema,
     so a model that answers 0 for "not stated" produces output that
     parses cleanly and validates cleanly -- and a job scored that way
     would accept every candidate, with nothing anywhere reporting it.
     This is the one thing a passing schema check cannot catch on its
     own.

Timeout is 45s per call, same as gemini_isolate.py and for the same
reason: long enough to tell "slow" from "hung", short enough that
three failures do not cost most of a debugging session.

ENRICHMENT_SCHEMA and ENRICHMENT_PROMPT are defined here as
module-level constants specifically so that Part 2 (the real
enrichment pass) can import them rather than redefine them. What gets
proven against the live API in this script is what actually runs
against the 99 stored jobs -- a copy that drifts from this one would
make everything measured here meaningless.

Costs up to three API calls. Never prints the key.

    python -m scripts.enrichment_isolate

--job-id N is a fourth, separate mode. Stages A-C prove the client
against text this script hardcodes. That leaves one thing untested:
the real stored description. A job returning zero skills has two
explanations -- the posting genuinely naming none, or something going
wrong between the row and the request -- and hardcoded text cannot
tell them apart from each other. --job-id loads the real row, prints
build_job_document()'s output alongside its hash and the row's stored
skills_source_hash, and sends that exact text through
GeminiEnrichmentClient.enrich_job() -- the real client, not a
reimplementation, so this exercises the same code path the failing
run used. The hash comparison is what separates "we sent the wrong
text" from "we sent the right text and got nothing back". Costs ONE
API call.

    python -m scripts.enrichment_isolate --job-id 42
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from google import genai
from google.genai import types

from app.core.config import settings
from app.db.repositories.job import JobRepository
from app.db.session import dispose_engine, init_engine, session_scope
from app.integrations.gemini_embeddings import describe_genai_error
from app.integrations.gemini_enrichment import (
    ENRICHMENT_PROMPT,
    ENRICHMENT_SCHEMA,
    EnrichmentError,
    GeminiEnrichmentClient,
)
from app.services.embedding_text import build_job_document, document_hash
from app.services.job_enrichment_rules import filter_and_normalize_skills, infer_work_mode

# 90s, raised from 45s after measurement rather than by feel.
#
# The same three calls, run twice with nothing changed between them
# except the skills paragraph of the prompt, took:
#
#   A ("Reply with the word pong", identical both times)  29.2s  37.9s
#   B (prompt got LONGER)                                 28.3s   7.4s
#   C                                                     32.3s  45.0s
#
# Elapsed time here is not a function of the request. A did not
# change at all and moved by nine seconds; B grew and got four times
# faster. Six measurements span 7s to 45s.
#
# So a 45s ceiling sits INSIDE the observed range, which makes the
# timeout itself a coin flip rather than a diagnostic: a run that
# crosses it reports "hung" for something that was merely slow, and
# on the enrichment path that would increment
# skills_extraction_attempts and quietly drop the row out of the
# default retry filter forever.
#
# 90s is twice the largest value seen. Same reasoning as Day 7's 7.0
# second pacing over 6.0: a threshold sitting on its observed
# boundary is the one that fails while looking like it should pass.
TIMEOUT_MS = 90_000

SCHEMA_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": ENRICHMENT_SCHEMA,
}

STAGE_B_DESCRIPTION = (
    "Machine Learning Engineer. 2-4 years of experience required. "
    "Python, PyTorch, and experience deploying models to production."
)

# Deliberately names technologies and NO years of experience.
STAGE_C_DESCRIPTION = (
    "We are looking for a backend engineer to join our platform team. "
    "You will build and maintain services in Python using FastAPI, "
    "work with PostgreSQL and Redis, and help move our deployment "
    "pipeline onto Kubernetes. Familiarity with Docker and CI/CD is "
    "expected. You will collaborate closely with the data team on "
    "internal APIs. Strong communication skills required."
)

STAGE_B_JOB_ID = 4242
STAGE_C_JOB_ID = 9999


def build_input(job_id: int, description: str) -> str:
    """Assemble the prompt text sent for one enrichment call."""
    return (
        ENRICHMENT_PROMPT
        + f"\njob_id: {job_id}\n\n--- JOB POSTING ---\n{description}\n"
    )


async def attempt(client, label: str, description: str, **kwargs) -> tuple[bool, float, str | None]:
    """Run one create() call and report how it ended.

    Returns (success, elapsed_seconds, output_text). The elapsed time
    matters as much as the outcome: a call that fails near the 45s
    timeout was hung, while one that fails in a couple of seconds was
    refused, and those point in opposite directions.
    """
    print(f"--- {label}: {description}")
    started = time.monotonic()

    try:
        interaction = await client.aio.interactions.create(
            model=settings.gemini_model,
            **kwargs,
        )
    except Exception as error:  # noqa: BLE001 - this script exists to see any error
        elapsed = time.monotonic() - started
        print(f"    elapsed: {elapsed:.3f}s")
        print("    success: False")
        print(f"    {type(error).__name__}: {describe_genai_error(error)}")
        print()
        return False, elapsed, None

    elapsed = time.monotonic() - started
    text = getattr(interaction, "output_text", None)
    success = interaction.status == "completed" and text is not None

    print(f"    elapsed: {elapsed:.3f}s")
    print(f"    status: {interaction.status!r}")
    print(f"    success: {success}")

    if text is None:
        print("    output_text: None")
        print(f"    errors: {getattr(interaction, 'errors', None)}")
    else:
        print(f"    output_text: {text[:200]!r}")

    print()
    return success, elapsed, text


def _parse(label: str, text: str) -> dict | None:
    """Parse a stage's output_text as JSON, reporting failure without raising."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        print(f"    {label} output_text did not parse as JSON: {error}")
        print()
        return None

    print(f"    {label} parsed: {parsed}")
    return parsed


async def main() -> int:
    print(f"model: {settings.gemini_model}")
    print(f"timeout: {TIMEOUT_MS / 1000:.0f}s per call")
    print()

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )

    ok_a, _, _ = await attempt(
        client,
        "A",
        "tiny input, no schema",
        input="Reply with the word pong.",
    )

    if not ok_a:
        print("A failed. This account cannot reach settings.gemini_model at")
        print("all today. Nothing about the schema is involved.")
        return 1

    ok_b, _, text_b = await attempt(
        client,
        "B",
        "short job description, with the enrichment schema",
        input=build_input(STAGE_B_JOB_ID, STAGE_B_DESCRIPTION),
        response_format=SCHEMA_FORMAT,
    )

    if not ok_b:
        print("A passed but B failed. The four-field schema is the cause.")
        return 1

    parsed_b = _parse("B", text_b)
    if parsed_b is None:
        return 1

    print(f"B job_id echoed correctly: {parsed_b.get('job_id') == STAGE_B_JOB_ID}")
    print(f"B min: {parsed_b.get('min_experience_years')}")
    print(f"B max: {parsed_b.get('max_experience_years')}")
    skills_b = parsed_b.get("skills", [])
    skills_b_over_three_words = [s for s in skills_b if len(s.split()) > 3]
    print(f"B skills over three words: {skills_b_over_three_words}")
    print(f"B skills over three words count: {len(skills_b_over_three_words)}")
    print()

    ok_c, _, text_c = await attempt(
        client,
        "C",
        "description names technologies, states no experience, with schema",
        input=build_input(STAGE_C_JOB_ID, STAGE_C_DESCRIPTION),
        response_format=SCHEMA_FORMAT,
    )

    if not ok_c:
        print("A and B passed, C failed.")
        print("Do NOT conclude the cause from this alone. C is LONGER")
        print("than B, and C has already passed once at 32.3s with this")
        print("same text. Elapsed times here span 7s to 45s for calls")
        print("that did not change, so a single failure is as consistent")
        print("with variance as with anything about C.")
        print("Re-run before drawing any conclusion.")
        return 1

    parsed_c = _parse("C", text_c)
    if parsed_c is None:
        return 1

    skills_c = parsed_c.get("skills", [])
    print(f"C job_id echoed correctly: {parsed_c.get('job_id') == STAGE_C_JOB_ID}")
    print(f"C min_experience_years is None: {parsed_c.get('min_experience_years') is None}")
    print(f"C max_experience_years is None: {parsed_c.get('max_experience_years') is None}")
    print(f"C skills count: {len(skills_c)}")
    print(f"C skills: {skills_c}")
    skills_c_over_three_words = [s for s in skills_c if len(s.split()) > 3]
    print(f"C skills over three words: {skills_c_over_three_words}")
    print(f"C skills over three words count: {len(skills_c_over_three_words)}")
    print()

    print("All three stages succeeded.")
    return 0


async def run_job_id_mode(job_id: int) -> int:
    """One real API call against a stored job row, instead of the
    hardcoded stage B/C descriptions.

    See the module docstring for why this mode exists. Costs ONE API
    call, and only makes it once the hash comparison below has been
    printed for inspection.
    """
    if init_engine() is None:
        print("DATABASE_URL is not configured.")
        return 1

    try:
        async with session_scope() as session:
            job = await JobRepository(session).by_id(job_id)

        if job is None:
            print(f"No job with id {job_id}.")
            return 1

        document = build_job_document(job.title, job.description)
        digest = document_hash(document)

        print(f"job id: {job.id}")
        print(f"title: {job.title}")
        print(f"description length: {len(job.description or '')}")
        print("--- document ---")
        print(document)
        print("--- end document ---")
        print(f"document_hash: {digest}")
        print(f"stored skills_source_hash: {job.skills_source_hash}")
        print(f"hashes equal: {digest == job.skills_source_hash}")
        print()

        client = GeminiEnrichmentClient()

        started = time.monotonic()
        try:
            enrichment = await client.enrich_job(job.id, document)
        except EnrichmentError as error:
            # EnrichmentError subclasses already carry a message built
            # from describe_genai_error() or from nothing but two job
            # ids (EnrichmentIdMismatch) -- safe to print as-is, unlike
            # a raw provider exception.
            elapsed = time.monotonic() - started
            print(f"elapsed: {elapsed:.3f}s")
            print(f"FAILED: {type(error).__name__}: {error}")
            return 1

        elapsed = time.monotonic() - started
        print(f"elapsed: {elapsed:.3f}s")
        print(f"result: {enrichment!r}")
        print()

        filtered = filter_and_normalize_skills(enrichment.skills)
        print(f"kept: {filtered.kept}")
        print(f"dropped_too_long: {filtered.dropped_too_long}")
        print(f"dropped_soft: {filtered.dropped_soft}")
        print()

        work_mode = infer_work_mode(job.title, job.location, job.description)
        print(f"work_mode: {work_mode}")

        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="Skip stages A-C and run one real call against this stored job's row.",
    )
    args = parser.parse_args()

    if args.job_id is not None:
        # Only this path opens a database connection (to load the
        # stored job row) -- psycopg's async driver cannot use the
        # ProactorEventLoop Windows defaults to. The default no-flag
        # path below is pure HTTP and never touches a database, so
        # the policy is set only here rather than unconditionally.
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        raise SystemExit(asyncio.run(run_job_id_mode(args.job_id)))

    raise SystemExit(asyncio.run(main()))
