"""Three escalating Gemini calls, timed, to find where the hang starts.

Context: extraction hangs for the full 120s timeout on a 3901-character
prompt. The SDK surface, the model name and the timeout wiring have all
been verified correct by reading the installed package, and this same
code succeeded live during Day 4.5. So the fault is in the request
itself, the account, or the route — not in the method being called.

Each call isolates one variable:

  A. interactions.create, tiny input, NO response_format.
     Answers: does this account reach this model at all?

  B. interactions.create, tiny input, WITH the real response schema.
     Answers: is the schema what makes it hang? _build_response_schema()
     inlines $defs and adds a required list; a schema the service
     dislikes could plausibly leave the request pending rather than
     rejecting it.

  C. interactions.create, the real CV prompt, WITH the real schema.
     Answers: is it only the full-size request that fails?

Timeout is 45s per call rather than 120 — long enough to tell "slow"
from "hung", short enough that three failures do not cost six minutes.
Whichever letter first fails is the answer.

Costs up to three API calls. Never prints the key.

    python gemini_isolate.py
"""

from __future__ import annotations

import asyncio
import sys
import time

from google import genai
from google.genai import types

from app.core.config import settings
from app.integrations.gemini import EXTRACTION_PROMPT, _build_response_schema

TIMEOUT_MS = 45_000

SCHEMA_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": _build_response_schema(),
}

TINY_CV = (
    "Rachit Kapoor. AI/ML Engineer, Delhi. "
    "Skills: Python, PyTorch. "
    "Experience: ML Intern at Acme, Jan 2024 to Present."
)


async def attempt(client, label: str, description: str, **kwargs) -> bool:
    """Run one create() call and report how it ended.

    Returns True on success. The elapsed time matters as much as the
    outcome: a call that fails at 45.0s was hung, while one that fails
    at 2s was refused, and those point in opposite directions.
    """
    print(f"--- {label}: {description}")
    started = time.monotonic()

    try:
        interaction = await client.aio.interactions.create(
            model=settings.gemini_model,
            **kwargs,
        )
    except Exception as error:  # noqa: BLE001 - this script exists to see any error
        print(f"    FAILED after {time.monotonic() - started:.1f}s")
        print(f"    {type(error).__name__}: {error}")
        print()
        return False

    elapsed = time.monotonic() - started
    print(f"    returned in {elapsed:.1f}s")
    print(f"    status: {interaction.status!r}")

    # status is the thing to look at. The type allows 'queued' and
    # 'in_progress', which would mean create() returns a handle to work
    # that has not finished — a completely different failure than a
    # network hang, and one no amount of timeout tuning would fix.
    text = getattr(interaction, "output_text", None)
    if text is None:
        print("    output_text: None")
        print(f"    errors: {getattr(interaction, 'errors', None)}")
    else:
        print(f"    output_text: {text[:120]!r}")

    print()
    return interaction.status == "completed" and text is not None


async def main() -> int:
    print(f"model: {settings.gemini_model}")
    print(f"timeout: {TIMEOUT_MS / 1000:.0f}s per call")
    print()

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )

    ok_a = await attempt(
        client,
        "A",
        "tiny input, no schema",
        input="Reply with the single word: pong",
    )

    if not ok_a:
        print("A failed. Nothing about the CV or the schema is involved —")
        print("this account cannot reach this model. Check the key is")
        print("active and that the model is enabled for it.")
        return 1

    ok_b = await attempt(
        client,
        "B",
        "tiny input, real schema",
        input=EXTRACTION_PROMPT.format(text=TINY_CV),
        response_format=SCHEMA_FORMAT,
    )

    if not ok_b:
        print("A passed but B failed. The response schema is the cause.")
        print("_build_response_schema() is what to look at next.")
        return 1

    ok_c = await attempt(
        client,
        "C",
        "full CV prompt, real schema",
        input=EXTRACTION_PROMPT.format(text=TINY_CV * 30),
        response_format=SCHEMA_FORMAT,
    )

    if not ok_c:
        print("A and B passed, C failed. Size is the cause, not the")
        print("schema — the request needs longer than the timeout, or")
        print("the account is being throttled on larger prompts.")
        return 1

    print("All three succeeded. Gemini is healthy right now; the earlier")
    print("failures were transient. Re-run the real extraction.")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    raise SystemExit(asyncio.run(main()))