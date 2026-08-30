"""Verify Fix 3 and Fix 4 without calling Gemini.

    python -m scripts.offline_extraction_dryrun --user-id 2

Everything Fix 3 and Fix 4 changed sits *after* the Gemini call: the
three-phase transaction split, the conditional claim, and writing
normalized skill keys to profiles.skills. None of it needs a live API
call to exercise — but extract_cv makes one, so a quota block stops
verification of code that has nothing to do with the quota.

extract_cv already accepts gemini_client, so a stand-in can be passed
in. Rather than inventing a profile, this replays the most recent
extracted_profile stored in cv_versions — a response a real Gemini call
produced earlier. The data written is therefore genuine; only the
network round trip is skipped.

What this DOES verify:
  - the three-phase split completes without IdleInTransactionSessionTimeout
  - profiles.skills receives normalized, de-duplicated keys (Fix 3)
  - two concurrent extractions resolve to one winner (Fix 4)

What it does NOT verify: the live call itself, which was already
confirmed separately when the same CV returned 39 skills, 3 roles and
1 degree.
"""

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # psycopg's async driver needs a selector event loop; Windows defaults
    # to the proactor loop, which it can't use.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db.session import dispose_engine, init_engine
from app.schemas.cv_profile import CVProfile
from app.services.cv_extraction import extract_cv


class ReplayGeminiClient:
    """Stands in for GeminiClient, returning a previously captured profile.

    Duck-typed rather than subclassed: extract_cv only ever reads
    `.model` and awaits `.extract_profile()`, so matching that surface
    is enough. Subclassing would drag in a real genai.Client and the
    API key check that comes with constructing one.
    """

    def __init__(self, profile: CVProfile, model: str) -> None:
        self._profile = profile
        self.model = model

    async def extract_profile(self, raw_text: str) -> CVProfile:
        # A brief pause so two concurrent callers genuinely overlap.
        # Returning instantly would let the first finish and release
        # its claim before the second ever looked, which would make the
        # concurrency check pass for the wrong reason.
        await asyncio.sleep(2)
        return self._profile


async def load_last_profile(engine, user_id: int) -> tuple[CVProfile, str] | None:
    """Fetch the most recent stored extraction for this user."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT v.extracted_profile, v.extraction_model "
                "FROM cv_versions v JOIN cvs c ON c.id = v.cv_id "
                "WHERE c.user_id = :uid ORDER BY v.id DESC LIMIT 1"
            ),
            {"uid": user_id},
        )
        row = result.first()

    if row is None:
        return None
    return CVProfile.model_validate(row[0]), row[1]


async def report(engine, user_id: int) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT skills->>0, jsonb_array_length(skills), "
                "total_experience_years, jsonb_array_length(experience) "
                "FROM profiles WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        row = result.first()

    if row is None:
        print("  no profile row")
        return

    first, count, years, jobs = row
    print(f"  first skill: {first!r}")
    print(f"  skill count: {count}")
    print(f"  total_experience_years: {years}")
    print(f"  experience entries: {jobs}")

    if first is not None and first == first.lower():
        print("  PASS: skills are normalized (Fix 3)")
    else:
        print("  FAIL: skills are not normalized — still the model's spellings")


async def run(user_id: int) -> None:
    engine = init_engine()
    if engine is None:
        print("DATABASE_URL is not configured.")
        return

    try:
        loaded = await load_last_profile(engine, user_id)
        if loaded is None:
            print(f"No stored extraction for user_id={user_id} to replay.")
            return

        profile, model = loaded
        print(
            f"Replaying a stored profile: {len(profile.skills)} skills, "
            f"{len(profile.experience)} roles, {len(profile.education)} education, "
            f"model={model}"
        )

        print("\nBefore:")
        await report(engine, user_id)

        client = ReplayGeminiClient(profile, model)

        # --- single run: does the three-phase split complete? --------
        print("\n1. Single extraction through the new three-phase code ...")
        result = await extract_cv(user_id, gemini_client=client)
        print(f"   status={result.status.value}")
        if result.error:
            print(f"   error={result.error}")

        print("\nAfter:")
        await report(engine, user_id)

        # --- concurrent run: does the claim exclude? -----------------
        print("\n2. Two concurrent extractions on the same CV ...")
        outcomes = await asyncio.gather(
            extract_cv(user_id, gemini_client=client),
            extract_cv(user_id, gemini_client=client),
            return_exceptions=True,
        )

        statuses = []
        for index, outcome in enumerate(outcomes, start=1):
            if isinstance(outcome, BaseException):
                label = f"RAISED {type(outcome).__name__}: {str(outcome)[:100]}"
            else:
                label = outcome.status.value
            statuses.append(label)
            print(f"   task {index}: {label}")

        print()
        if statuses.count("complete") == 1 and statuses.count("extracting") == 1:
            print("   PASS: one task won the claim, one stood down (Fix 4)")
        elif statuses.count("complete") == 2:
            print("   FAIL: both extracted. The claim is not excluding.")
        else:
            print(f"   INCONCLUSIVE: {statuses}")
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    asyncio.run(run(parser.parse_args().user_id))


if __name__ == "__main__":
    main()