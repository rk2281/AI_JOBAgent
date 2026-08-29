"""Reads and writes for the normalized skills catalog."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.skill import Skill

# A handful of punctuation variants that would otherwise register as
# different skills purely because of how someone typed them. This is
# not an attempt at a full synonym dictionary — deciding whether "GCP"
# and "Google Cloud Platform" are the same skill is a much fuzzier
# problem that belongs to Day 6/7 matching, not to storing what a CV
# literally said.
PUNCTUATION_ALIASES: dict[str, str] = {
    "node.js": "nodejs",
    "c++": "cpp",
    "c#": "csharp",
    ".net": "dotnet",
    "ci/cd": "cicd",
    "next.js": "nextjs",
}


def normalize_skill_name(name: str) -> str:
    """Fold a skill name to its catalog key.

    Lowercases, collapses whitespace, and applies PUNCTUATION_ALIASES.
    Two CVs spelling the same skill "Node.js" and "NodeJS" must land
    on the same skills row, or a job requiring one spelling would
    silently fail to match a candidate who used the other.
    """
    cleaned = " ".join(name.strip().lower().split())
    return PUNCTUATION_ALIASES.get(cleaned, cleaned)


def normalize_skill_names(names: list[str]) -> list[str]:
    """Normalize a list of skill names, dropping duplicates.

    De-duplication is the reason this exists rather than a bare list
    comprehension at the call site. Normalization is lossy by design —
    "Node.js" and "NodeJS" both fold to "nodejs" — so a CV listing
    both, or listing a skill once under a heading and again in a
    summary, would otherwise store the same key twice. A duplicate key
    in profiles.skills would let one skill count twice in a match
    score, quietly inflating a candidate against everyone else.

    Insertion order is preserved. It carries a weak signal about what
    the candidate leads with, and reordering to something arbitrary
    would discard that for no gain.
    """
    seen: dict[str, None] = {}
    for name in names:
        normalized = normalize_skill_name(name)
        if normalized:
            seen.setdefault(normalized, None)
    return list(seen)


class SkillRepository:
    """All database access concerning a Skill row."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, name: str) -> Skill:
        """Return the catalog row for `name`, creating one if absent.

        Uses INSERT ... ON CONFLICT DO NOTHING against the unique
        index on normalized_name, for the same reason as
        UserRepository.get_or_create: two CVs can be extracted
        concurrently, and two skills normalizing to the same key
        racing each other must not raise IntegrityError.
        """
        normalized = normalize_skill_name(name)

        existing = await self._get_by_normalized_name(normalized)
        if existing is not None:
            return existing

        statement = (
            insert(Skill)
            .values(name=name.strip(), normalized_name=normalized)
            .on_conflict_do_nothing(index_elements=["normalized_name"])
            .returning(Skill)
        )

        result = await self._session.execute(statement)
        created = result.scalar_one_or_none()

        if created is not None:
            return created

        # The conflict fired: another extraction inserted this skill
        # between our SELECT and our INSERT. Read the winner's row.
        await self._session.flush()
        skill = await self._get_by_normalized_name(normalized)
        assert skill is not None, "row vanished after ON CONFLICT DO NOTHING"
        return skill

    async def _get_by_normalized_name(self, normalized_name: str) -> Skill | None:
        result = await self._session.execute(
            select(Skill).where(Skill.normalized_name == normalized_name)
        )
        return result.scalar_one_or_none()
