"""Five escalating Gemini embedding calls, to settle Day 7's unknowns.

Day 7 stores embeddings in a vector(768) column with an HNSW index on
vector_cosine_ops. Four things must be known before that code is
written, and guessing any of them costs a migration, a reindex and a
full re-embed:

  A. What does the installed SDK's embedding surface look like?
     No network call. Introspects google-genai and prints the method
     signature and config fields, so that a TypeError in a later stage
     can be read against what actually exists rather than what was
     assumed.

  B. Which models does this API key list as supporting embedContent?
     Answers the model-name question definitively. A model name this
     key cannot serve has previously HUNG rather than 404'd, so it is
     worth reading the list instead of trying names.

  C. One short text, default config.
     Answers: what dimension comes back, and is it unit-normalised?

  D. One short text, output_dimensionality=768.
     Answers: is truncation accepted, and is the truncated vector
     still unit-normalised? Matryoshka outputs typically are not, and
     an unnormalised vector is fine for the <=> cosine operator but
     wrong for anything that takes a raw dot product.

  E. task_type applied or silently ignored?
     The same text embedded as RETRIEVAL_DOCUMENT and as
     RETRIEVAL_QUERY, compared. This parameter has no error path: if
     the API ignores it, nothing fails, the vectors are merely worse,
     and it surfaces on Day 8 as a scoring problem debugged in the
     wrong place. A cosine of exactly 1.000000 between the two means
     ignored.

  F. Batch behaviour.
     Answers: does one call accept a list, does it return one vector
     per input, and does it preserve order? Order is checked by
     embedding [X, Y, X] and asserting result[0] == result[2] != [1].
     A batch that silently reordered would attach every job's vector to
     the wrong job, and nothing downstream would ever notice.

Costs up to 2 + (batch size) embedding calls' worth of quota. Run the
whole thing once, then use --stage to re-run one stage without paying
for the others.

No database connection, so no WindowsSelectorEventLoopPolicy here --
that is only needed by scripts that open a psycopg connection.

Never prints the API key, a URL, or a raw exception.

    python -m scripts.embedding_isolate
    python -m scripts.embedding_isolate --model gemini-embedding-001
    python -m scripts.embedding_isolate --stage E
    python -m scripts.embedding_isolate --stage F --batch-size 32
"""

from __future__ import annotations

import argparse
import inspect
import math
import time
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings

# The dimension Day 7's column and HNSW index were built for. Not a
# preference: pgvector's HNSW index does not support more than 2000
# dimensions, so a larger native output cannot be indexed at all.
TARGET_DIMENSION = 768

# A starting guess only. Stage B prints what this key actually lists,
# and whatever that says wins over this constant.
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"

TIMEOUT_MS = 45_000

# Two texts that are clearly about different things, so a similarity
# between them is interpretable. Deliberately shaped like a job posting
# and like a CV summary, because that is the actual comparison Day 7
# exists to make.
TEXT_JOB = (
    "Job title: Machine Learning Engineer\n\n"
    "Description: Build and deploy ML models in production. "
    "Python, PyTorch and cloud experience required."
)
TEXT_OTHER = (
    "Job title: Restaurant Floor Manager\n\n"
    "Description: Supervise front-of-house staff, manage rotas and "
    "handle customer complaints in a busy city-centre venue."
)


def describe_genai_error(error: Exception) -> str:
    """Describe a provider error without formatting the error object.

    Reads only `code` and `message`, the two fields a google-genai
    APIError carries. This mirrors _format_errors() in
    app/integrations/gemini.py and describe_http_error() in
    app/integrations/http_errors.py, and it exists for the same reason:
    an exception's string form can carry the request it came from, and
    the request can carry a credential or a user's personal data. An
    error object is not safe to format just because it is an error.
    """
    parts = [type(error).__name__]

    code = getattr(error, "code", None)
    if code is not None:
        parts.append(f"code={code}")

    message = getattr(error, "message", None)
    if isinstance(message, str) and message:
        parts.append(message)

    return " | ".join(parts)


def l2_norm(vector: list[float]) -> float:
    """Euclidean length. 1.0 means the provider returned a unit vector."""
    return math.sqrt(sum(component * component for component in vector))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity, computed without assuming either side is unit length.

    Returns 0.0 if either vector has zero length rather than raising.
    A zero vector is a real failure mode worth reporting as a number
    instead of a traceback: it is equidistant from everything, so a
    stored zero vector would place that row at an arbitrary point in
    every ranking while looking perfectly valid in the column.
    """
    left_norm = l2_norm(left)
    right_norm = l2_norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


def _extract_vectors(response: Any) -> list[list[float]]:
    """Pull plain float lists out of whatever shape the SDK returned.

    Written defensively on purpose. This script's whole job is to run
    before the SDK's embedding response shape has been confirmed, so it
    must not crash on an attribute name that turns out to be different
    -- it must report what it found.
    """
    embeddings = getattr(response, "embeddings", None)
    if embeddings is None:
        single = getattr(response, "embedding", None)
        embeddings = [single] if single is not None else []

    vectors: list[list[float]] = []
    for item in embeddings:
        values = getattr(item, "values", None)
        if values is None and isinstance(item, (list, tuple)):
            values = item
        if values is None:
            continue
        vectors.append([float(v) for v in values])

    return vectors


def _report_vector(label: str, vector: list[float]) -> None:
    norm = l2_norm(vector)
    print(f"    {label}: dimension={len(vector)}  l2_norm={norm:.6f}")

    if len(vector) == TARGET_DIMENSION:
        print(f"      -> matches the vector({TARGET_DIMENSION}) column")
    else:
        print(
            f"      -> DOES NOT match the vector({TARGET_DIMENSION}) column; "
            "Postgres will reject this on insert"
        )

    # Exact zero, not a small threshold. A genuinely small norm can be
    # legitimate; a norm of exactly zero cannot, and testing the
    # boundary with < would flag healthy vectors as broken.
    if norm == 0.0:
        print("      -> ZERO VECTOR. Equidistant from everything. Never store this.")
    elif abs(norm - 1.0) < 1e-4:
        print("      -> unit-normalised")
    else:
        print("      -> NOT unit-normalised; normalise before storing")


# --- stages ---------------------------------------------------------


def stage_a() -> bool:
    """Introspect the SDK. No network call, so this cannot cost quota."""
    print("--- A: SDK surface (no network call)")

    version = getattr(genai, "__version__", "unknown")
    print(f"    google-genai version: {version}")

    client = genai.Client(api_key=settings.gemini_api_key)

    embed_names = [name for name in dir(client.aio.models) if "embed" in name.lower()]
    print(f"    embedding methods on client.aio.models: {embed_names}")

    if not embed_names:
        print("    No embedding method found on client.aio.models.")
        print("    Everything below will fail. Check the installed SDK version.")
        print()
        return False

    method = getattr(client.aio.models, embed_names[0], None)
    if method is not None:
        try:
            print(f"    signature: {embed_names[0]}{inspect.signature(method)}")
        except (TypeError, ValueError):
            print("    signature: not introspectable")

    config_names = [name for name in dir(types) if "Embed" in name]
    print(f"    Embed* types available: {config_names}")

    config_type = getattr(types, "EmbedContentConfig", None)
    if config_type is not None:
        fields = getattr(config_type, "model_fields", None)
        if fields:
            print(f"    EmbedContentConfig fields: {sorted(fields)}")

    print()
    return True


def stage_b(model: str) -> bool:
    """List models this key can see, and say whether `model` is among them."""
    print("--- B: models this key lists as supporting embedding")

    # Synchronous client on purpose. Listing is a one-off diagnostic and
    # the async pager's await shape is one more unverified thing this
    # script does not need to depend on.
    client = genai.Client(api_key=settings.gemini_api_key)

    try:
        listed = list(client.models.list())
    except Exception as error:  # noqa: BLE001 - this script exists to see any error
        print(f"    FAILED: {describe_genai_error(error)}")
        print("    Could not list models. This is a key or connectivity")
        print("    problem, not a model-name problem. Nothing below will work.")
        print()
        return False

    embedders = []
    for entry in listed:
        actions = getattr(entry, "supported_actions", None) or []
        if any("embed" in str(action).lower() for action in actions):
            embedders.append(getattr(entry, "name", "?"))

    if embedders:
        print(f"    {len(embedders)} model(s) support embedding:")
        for name in sorted(embedders):
            print(f"      {name}")
    else:
        print(f"    None of the {len(listed)} listed models advertise embedding.")
        print("    supported_actions may not be populated by this SDK version;")
        print("    stage C will settle it by simply making the call.")

    match = [name for name in embedders if model in name]
    print(f"    requested model {model!r}: {'listed' if match else 'NOT listed'}")

    print()
    return True


async def _embed(
    client: genai.Client,
    model: str,
    contents: list[str],
    **config_kwargs: Any,
) -> list[list[float]] | None:
    """One embedding call. Returns the vectors, or None if it failed."""
    started = time.monotonic()

    config = None
    if config_kwargs:
        config_type = getattr(types, "EmbedContentConfig", None)
        if config_type is None:
            print("    types.EmbedContentConfig does not exist in this SDK.")
            return None
        config = config_type(**config_kwargs)

    try:
        response = await client.aio.models.embed_content(
            model=model,
            contents=contents,
            config=config,
        )
    except Exception as error:  # noqa: BLE001 - this script exists to see any error
        elapsed = time.monotonic() - started
        print(f"    FAILED after {elapsed:.1f}s: {describe_genai_error(error)}")
        # Elapsed time carries as much information as the failure. A
        # call that dies at 45.0s was hung; one that dies at 1.2s was
        # refused. Those point in opposite directions.
        return None

    elapsed = time.monotonic() - started
    vectors = _extract_vectors(response)
    print(f"    returned {len(vectors)} vector(s) in {elapsed:.1f}s")
    return vectors


async def stage_c(client: genai.Client, model: str) -> list[float] | None:
    """One text, default config. What dimension does this model return?"""
    print("--- C: one text, default config")

    vectors = await _embed(client, model, [TEXT_JOB])
    if not vectors:
        print("    C failed. Nothing about dimensions or task types is")
        print("    involved yet -- this key cannot embed with this model.")
        print("    Check stage B's list for a name that is actually served.")
        print()
        return None

    _report_vector("default", vectors[0])
    print()
    return vectors[0]


async def stage_d(client: genai.Client, model: str) -> list[float] | None:
    """One text, output_dimensionality=768. Is truncation accepted?"""
    print(f"--- D: one text, output_dimensionality={TARGET_DIMENSION}")

    vectors = await _embed(
        client,
        model,
        [TEXT_JOB],
        output_dimensionality=TARGET_DIMENSION,
    )
    if not vectors:
        print("    C passed but D failed. The model serves, but it will not")
        print(f"    produce {TARGET_DIMENSION} dimensions on request. Day 7 must")
        print("    either truncate and re-normalise client-side, or change the")
        print("    column -- which means a migration, an index rebuild and a")
        print("    full re-embed. Report this before Part 2 is written.")
        print()
        return None

    _report_vector(f"output_dimensionality={TARGET_DIMENSION}", vectors[0])
    print()
    return vectors[0]


async def stage_e(client: genai.Client, model: str) -> bool:
    """Is task_type applied, or silently ignored?

    The stage with no error path. Both calls will succeed either way;
    the answer is in the number.
    """
    print("--- E: task_type applied or ignored")

    document = await _embed(
        client,
        model,
        [TEXT_JOB],
        output_dimensionality=TARGET_DIMENSION,
        task_type="RETRIEVAL_DOCUMENT",
    )
    if not document:
        print("    RETRIEVAL_DOCUMENT was rejected outright.")
        print()
        return False

    query = await _embed(
        client,
        model,
        [TEXT_JOB],
        output_dimensionality=TARGET_DIMENSION,
        task_type="RETRIEVAL_QUERY",
    )
    if not query:
        print("    RETRIEVAL_QUERY was rejected outright.")
        print()
        return False

    similarity = cosine_similarity(document[0], query[0])
    print(f"    cosine(same text as DOCUMENT, same text as QUERY) = {similarity:.6f}")

    if similarity > 0.999999:
        print("    -> task_type is being IGNORED. The two vectors are identical.")
        print("       Day 7 should stop passing it and stop relying on it, and")
        print("       Day 8 should not expect the asymmetry to be doing any work.")
    else:
        print("    -> task_type IS applied. The CV side and the job side will")
        print("       be embedded differently, which is what makes a CV")
        print("       comparable to a job ad rather than merely to other CVs.")

    print()
    return True


async def stage_f(client: genai.Client, model: str, batch_size: int) -> bool:
    """Batch: one call, many texts. Count, dimensions, and ORDER."""
    print(f"--- F: batch of {batch_size}")

    # [X, Y, X, filler...]. If the API preserves order, vector 0 and
    # vector 2 are identical and both differ from vector 1. A batch
    # that silently reordered would attach every job's vector to the
    # wrong job, and no later check would catch it.
    contents = [TEXT_JOB, TEXT_OTHER, TEXT_JOB]
    contents += [f"Job title: Filler role {n}\n\nDescription: Placeholder." for n in range(batch_size - 3)]
    contents = contents[:batch_size]

    vectors = await _embed(
        client,
        model,
        contents,
        output_dimensionality=TARGET_DIMENSION,
        task_type="RETRIEVAL_DOCUMENT",
    )
    if not vectors:
        print(f"    A batch of {batch_size} was rejected. Try a smaller --batch-size")
        print("    to find the ceiling. Re-run with --stage F only; there is no")
        print("    reason to pay for C, D and E again.")
        print()
        return False

    if len(vectors) != len(contents):
        print(f"    MISMATCH: sent {len(contents)} texts, got {len(vectors)} vectors.")
        print("    A batch that does not return one vector per input cannot be")
        print("    zipped back onto its rows. Day 7 must embed one at a time.")
        print()
        return False

    wrong_dimension = [i for i, v in enumerate(vectors) if len(v) != TARGET_DIMENSION]
    if wrong_dimension:
        print(f"    {len(wrong_dimension)} vector(s) had the wrong dimension.")
        print()
        return False

    print(f"    all {len(vectors)} vectors have dimension {TARGET_DIMENSION}")

    if len(vectors) >= 3:
        same = cosine_similarity(vectors[0], vectors[2])
        different = cosine_similarity(vectors[0], vectors[1])
        print(f"    cosine(input 0, input 2) = {same:.6f}   (same text, expect ~1.0)")
        print(f"    cosine(input 0, input 1) = {different:.6f}   (different text, expect lower)")

        if same > 0.9999 and different < same:
            print("    -> order preserved, and similarity behaves sensibly")
        else:
            print("    -> ORDER IS NOT SAFE. Do not zip batch results back onto")
            print("       rows positionally. This would mis-assign every vector.")
            print()
            return False

    print()
    return True


# --- entry point ----------------------------------------------------


async def main(model: str, stage: str | None, batch_size: int) -> int:
    print(f"model: {model}")
    print(f"target dimension: {TARGET_DIMENSION}")
    print()

    def wanted(letter: str) -> bool:
        return stage is None or stage.upper() == letter

    if wanted("A") and not stage_a():
        return 1

    if wanted("B") and not stage_b(model):
        return 1

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )

    if wanted("C") and await stage_c(client, model) is None:
        return 1

    if wanted("D") and await stage_d(client, model) is None:
        return 1

    if wanted("E") and not await stage_e(client, model):
        return 1

    if wanted("F") and not await stage_f(client, model, batch_size):
        return 1

    print("Every requested stage passed. Report the printed dimensions,")
    print("norms and the stage E cosine before Part 2 is written -- those")
    print("three numbers are what Part 2's migration depends on.")
    return 0


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--stage", default=None, help="Run one stage only: A-F")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    # No WindowsSelectorEventLoopPolicy. This script opens no database
    # connection, and that policy exists only for psycopg's async driver.

    raise SystemExit(asyncio.run(main(args.model, args.stage, args.batch_size)))
