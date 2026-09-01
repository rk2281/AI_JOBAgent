"""Separate the five things that can make job ingestion return nothing.

Run me before debugging ingestion, not after. When a run produces no
jobs the possible causes are: this machine's network, Adzuna being
down, wrong credentials, an exhausted monthly quota, or a search query
that genuinely matches nothing. All five look identical from the far
end of the pipeline, and guessing between them one change at a time is
how a previous day of this project lost three hours.

Three escalating calls, each ruling out one layer:

  Step 1  no credentials at all  -> is the network up, is Adzuna up
  Step 2  credentials, 1 result  -> are the credentials valid, is
                                    there quota left
  Step 3  the real query         -> does the query match anything

Step 1 EXPECTS a 401 or 403 and treats it as a pass. That is the
point: a rejection proves a real server received the request and
answered it. Only a connection error or a timeout fails step 1.

Step 3 additionally measures the length of the descriptions that come
back. Adzuna truncates descriptions to an excerpt, and Day 7 builds
embeddings from that text. An embedding made from a 200-character
excerpt is a much weaker matching signal than one made from a full
posting, and it does not look broken anywhere -- it surfaces as
mediocre matches on Day 8, which is an expensive place to discover an
input problem. The number this prints is the input to that decision.

NOTHING IN THIS FILE PRINTS A URL. The Adzuna credentials are query
string parameters, so any URL printed here would carry both of them
into the terminal, into any pasted log, and into any screenshot.
str(httpx.HTTPStatusError) embeds the URL too, which is why errors go
through describe_http_error() instead of being formatted directly.

No WindowsSelectorEventLoopPolicy call here, unlike the other scripts
in this folder. That guard exists for psycopg's async driver, and this
script opens no database connection -- it is pure HTTP.

Usage:
    python -m scripts.source_isolate
    python -m scripts.source_isolate --what "data analyst" --where Gurgaon
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.http_errors import describe_http_error

BASE_URL = "https://api.adzuna.com/v1/api/jobs"
TIMEOUT_SECONDS = 30.0

# Below this, the description is an excerpt rather than a posting, and
# Day 7's embeddings will be built from a summary. Not a hard failure,
# but it changes what Day 8 can expect, so it is called out loudly.
DESCRIPTION_LENGTH_CONCERN = 500


def credentials_present() -> bool:
    """Report whether both Adzuna values are configured, without revealing them."""
    return bool(settings.adzuna_app_id) and bool(settings.adzuna_app_key)


def _search_url(page: int = 1) -> str:
    """Build the search endpoint URL. Never printed -- see module docstring."""
    return f"{BASE_URL}/{settings.adzuna_country}/search/{page}"


def _credentialed_params(**extra: Any) -> dict[str, Any]:
    """Query parameters including both credentials. Never printed."""
    return {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key,
        **extra,
    }


async def step_1_reachability(client: httpx.AsyncClient) -> bool:
    """Can this machine reach Adzuna at all?

    Deliberately sends no credentials. A 401 or 403 is a PASS: it means
    a real server received the request and made a decision about it,
    which is exactly what this step is asking. Only a transport failure
    or a timeout means the answer is no.
    """
    print("\n[Step 1] Reachability (no credentials sent)")
    try:
        response = await client.get(_search_url(), params={"results_per_page": 1})
    except Exception as error:  # noqa: BLE001 - any failure here is the answer
        print(f"  FAIL  {describe_http_error(error, timeout_seconds=TIMEOUT_SECONDS)}")
        print("  -> Network or Adzuna outage. Nothing further can be tested.")
        return False

    # 400 is what Adzuna actually returns for a request with no
    # credentials -- confirmed against the live API, where the
    # originally-expected 401/403 never appeared. All three mean the
    # same thing for this step's purpose: a real server received the
    # request and made a decision about it, which is exactly what
    # "reachable" means here.
    if response.status_code in (400, 401, 403):
        print(f"  PASS  HTTP {response.status_code} - rejected, as expected")
        print("  -> Adzuna is reachable and answering.")
        return True

    print(f"  PASS  HTTP {response.status_code} - reachable")
    print("  -> Unusual status for an uncredentialed call, but the server answered.")
    return True


async def step_2_credentials(client: httpx.AsyncClient) -> bool:
    """Are the credentials valid, and is there quota left?

    One result, no filters -- the smallest call that still proves the
    account works. 429 is separated from other failures on purpose: an
    exhausted quota is not a transient error and is not a bug in the
    query, and conflating it with either sends debugging in exactly the
    wrong direction.
    """
    print("\n[Step 2] Credentials and quota (1 result, no filters)")

    if not credentials_present():
        print("  FAIL  ADZUNA_APP_ID and/or ADZUNA_APP_KEY are not set")
        print("  -> Add them to .env, then run this again.")
        return False

    try:
        response = await client.get(
            _search_url(),
            params=_credentialed_params(results_per_page=1),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        print(f"  FAIL  {describe_http_error(error, timeout_seconds=TIMEOUT_SECONDS)}")
        if status in (401, 403):
            print("  -> Credentials rejected. Check them, or regenerate the pair.")
        elif status == 429:
            print("  -> QUOTA EXHAUSTED. Not transient, and not a bug in the")
            print("     query. Retrying spends nothing but does not help.")
        else:
            print("  -> Adzuna server-side error. Try again shortly.")
        return False
    except Exception as error:  # noqa: BLE001
        print(f"  FAIL  {describe_http_error(error, timeout_seconds=TIMEOUT_SECONDS)}")
        return False

    payload = response.json()
    total = payload.get("count")
    print(f"  PASS  HTTP 200, {len(payload.get('results', []))} result returned")
    print(f"  -> Credentials valid, quota available. Total jobs in {settings.adzuna_country}: {total}")
    return True


async def step_3_real_query(
    client: httpx.AsyncClient,
    what: str,
    where: str,
) -> bool:
    """Does the actual ingestion query match anything, and how good is the data?

    If steps 1 and 2 passed and this returns nothing, the problem is the
    query or Adzuna's coverage for this role in this city -- not the
    network, not the credentials, and not the parser. That elimination
    is the entire reason this script exists.
    """
    print(f"\n[Step 3] Real query: what={what!r} where={where!r} "
          f"max_days_old={settings.adzuna_max_days_old}")

    try:
        response = await client.get(
            _search_url(),
            params=_credentialed_params(
                what=what,
                where=where,
                max_days_old=settings.adzuna_max_days_old,
                results_per_page=settings.adzuna_results_per_page,
            ),
        )
        response.raise_for_status()
    except Exception as error:  # noqa: BLE001
        print(f"  FAIL  {describe_http_error(error, timeout_seconds=TIMEOUT_SECONDS)}")
        return False

    payload = response.json()
    results = payload.get("results", [])
    print(f"  PASS  HTTP 200, {len(results)} results on page 1 "
          f"(total matching: {payload.get('count')})")

    if not results:
        print("  -> Reachable, authorised, but this query matches nothing.")
        print("     Widen the keyword or the location. This is NOT a code bug.")
        return True

    _report_field_coverage(results)
    _report_description_lengths(results)
    _report_id_and_date_shape(results)
    return True


def _report_field_coverage(results: list[dict[str, Any]]) -> None:
    """How often is each field the ingestion pipeline needs actually present?

    A field that is present 60% of the time is a validation rule
    waiting to be written, and it is much cheaper to learn that here
    than from a run where two fifths of the jobs are silently rejected.
    """
    print("\n  Field coverage:")
    total = len(results)

    def present(job: dict[str, Any], *path: str) -> bool:
        node: Any = job
        for key in path:
            if not isinstance(node, dict):
                return False
            node = node.get(key)
        return bool(node)

    checks = {
        "id": ("id",),
        "title": ("title",),
        "company.display_name": ("company", "display_name"),
        "location.display_name": ("location", "display_name"),
        "description": ("description",),
        "redirect_url": ("redirect_url",),
        "created": ("created",),
    }

    for label, path in checks.items():
        count = sum(1 for job in results if present(job, *path))
        flag = "" if count == total else "   <-- not always present"
        print(f"    {label:<24} {count}/{total}{flag}")


def _report_description_lengths(results: list[dict[str, Any]]) -> None:
    """Measure how much text Day 7 will actually have to embed.

    Adzuna truncates descriptions. This turns that from a sentence in
    someone's blog post into a number measured against the real account
    and the real query, which is the only version worth relying on.
    """
    lengths = [len(job.get("description") or "") for job in results]
    lengths = [length for length in lengths if length > 0]

    if not lengths:
        print("\n  Description lengths: no descriptions returned at all.")
        return

    median = int(statistics.median(lengths))
    truncated = sum(
        1
        for job in results
        if (job.get("description") or "").rstrip().endswith(("…", "..."))
    )

    print("\n  Description lengths (characters):")
    print(f"    min {min(lengths)}   median {median}   max {max(lengths)}")
    print(f"    ending in an ellipsis: {truncated}/{len(results)}")

    # A hard cap and a short median are different shapes and only one
    # of them was being detected. The first version of this check read
    # `median < 500`, and the real median turned out to be exactly 500
    # -- so the check stayed silent in precisely the case it existed
    # to catch. A cap shows up as every length being identical AND
    # every description ending in an ellipsis, which is a much
    # stronger signal than any single number.
    capped = min(lengths) == max(lengths) and truncated == len(results)

    if capped:
        print(f"    NOTE: HARD CAP DETECTED at {max(lengths)} characters.")
        print("    Every description is the same length and every one ends in")
        print("    an ellipsis. These are excerpts, not postings. Day 7 must")
        print("    build embeddings from title + company + location +")
        print("    description rather than the description alone, and Day 8's")
        print("    skill signal cannot rely on the description containing a")
        print("    requirements list, because it does not.")
    elif median < DESCRIPTION_LENGTH_CONCERN:
        print(f"    NOTE: median is under {DESCRIPTION_LENGTH_CONCERN}. Day 7 will be")
        print("    embedding excerpts, not full postings. That weakens every")
        print("    semantic score downstream and it will not look like a fault.")
        print("    Record this in the Day 6 document as a known constraint.")


def _report_id_and_date_shape(results: list[dict[str, Any]]) -> None:
    """Check the two fields that the database schema constrains.

    jobs.external_id is NOT NULL and half of a unique constraint, so an
    absent or unstable id breaks ingestion at the insert. posted_at is
    DateTime(timezone=True), so a date without an offset has to be
    given one at the boundary -- a naive datetime here does not raise,
    it just sits 5h30m away from the truth, which is invisible at a
    14-day freshness window and wrong at a 1-day one.
    """
    sample = results[0]
    job_id = sample.get("id")
    created = sample.get("created")

    print("\n  Schema-critical fields (first result):")
    print(f"    id type: {type(job_id).__name__}")
    print(f"    created: {created!r}")

    if isinstance(created, str) and not (
        created.endswith("Z") or "+" in created[10:] or "-" in created[11:]
    ):
        print("    NOTE: no timezone offset visible. The posted_at column is")
        print("    timezone-aware; this will need an explicit UTC assumption")
        print("    at the integration boundary, written down as an assumption.")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Isolate why Adzuna ingestion returns nothing.",
    )
    parser.add_argument("--what", default="machine learning engineer")
    parser.add_argument("--where", default="Noida")
    args = parser.parse_args()

    print("=" * 68)
    print("Adzuna source isolation")
    print(f"  country          : {settings.adzuna_country}")
    print(f"  credentials set  : {credentials_present()}")
    print(f"  results per page : {settings.adzuna_results_per_page}")
    print("=" * 68)

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        if not await step_1_reachability(client):
            return
        if not await step_2_credentials(client):
            return
        await step_3_real_query(client, args.what, args.where)

    print("\n" + "=" * 68)
    print("Done. Each step that passed rules out one class of cause.")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
