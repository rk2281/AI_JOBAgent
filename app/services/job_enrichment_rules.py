"""Decide what a model's enrichment answer actually means.

GeminiEnrichmentClient's job is to get structured fields out of a
model: skills, min_experience_years, max_experience_years. Deciding
which of those "skills" entries count as a real skill, and what a
location or description string implies about work mode, are matching
rules, not client concerns -- the same split embedding_text.py draws
between building a document and deciding what goes in it. Keeping
that split here means these rules can be unit-tested against fixed
inputs with no database, no API key and no async runtime, and Part 2
can call them from a service that does own those things.

Every entry this module drops is COUNTED and returned to the caller
alongside what survived, never silently discarded. A filter that
quietly removes a quarter of a job's skills and reports nothing is
the same invisible-versus-wrong failure this project has paid for
before: the score still computes, still looks plausible, and is
wrong for a reason nobody can see without re-deriving it by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.db.repositories.skill import normalize_skill_name

# A live call returned "deploying models to production" as a skill.
# It is an activity, not a name. Three words admits "amazon web
# services" and "ansys fluent" -- both real catalog entries -- while
# still excluding phrases like that one.
#
# Compared with `> MAX_SKILL_WORDS`, so an entry of exactly three
# words is KEPT. Writing it as `>=` would drop "amazon web services",
# and the boundary is the case that fails while looking like it
# should pass.
MAX_SKILL_WORDS = 3


@dataclass(frozen=True)
class SkillFilterResult:
    """What filter_and_normalize_skills() decided about one job's list.

    Frozen for the same reason every other result type in this
    project is: a value that can be edited after the fact is one
    nobody can trace back to what the model actually said.

    dropped_too_long and dropped_soft keep the model's ORIGINAL
    wording, not the normalized key -- these exist for a human
    deciding whether the rule is too aggressive, and a normalized key
    reads badly for that purpose ("problem solving" is fine to read;
    a lowercased, alias-folded key is not what anyone typed).
    """

    kept: list[str]
    dropped_too_long: list[str]
    dropped_soft: list[str]


def filter_and_normalize_skills(raw_skills: list[str]) -> SkillFilterResult:
    """Turn a model's raw "skills" list into catalog-ready keys.

    Steps, in this exact order:

      a. drop blanks and anything that is not a string
      b. drop entries whose whitespace-split length is
         > MAX_SKILL_WORDS, into dropped_too_long
      c. drop entries whose lowercased form CONTAINS any term from
         settings.soft_skill_term_list, into dropped_soft
      d. pass survivors through normalize_skill_name()
      e. de-duplicate on the normalized key, preserving first-seen
         order

    The word-count test in step (b) runs on the ORIGINAL text, before
    normalization in step (d) -- deliberately, because normalization
    can change word count in general even though none of the
    punctuation aliases here do. The check is about phrases the model
    wrote, and the model's original wording is the only text that
    question is actually about.

    De-duplication in step (e) is not optional cleanup.
    normalize_skill_name() is lossy by design -- "Node.js" and
    "NodeJS" both fold to "nodejs" -- so a single response naming a
    skill twice under two spellings would otherwise store the same
    catalog key twice in one job's skill set, letting it count twice
    in the |job skills| denominator of the skill-match score.
    """
    kept: list[str] = []
    dropped_too_long: list[str] = []
    dropped_soft: list[str] = []
    seen: set[str] = set()

    for raw in raw_skills:
        if not isinstance(raw, str):
            continue

        text = raw.strip()
        if not text:
            continue

        if len(text.split()) > MAX_SKILL_WORDS:
            dropped_too_long.append(text)
            continue

        lowered = text.lower()
        if any(term in lowered for term in settings.soft_skill_term_list):
            dropped_soft.append(text)
            continue

        normalized = normalize_skill_name(text)
        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        kept.append(normalized)

    return SkillFilterResult(
        kept=kept,
        dropped_too_long=dropped_too_long,
        dropped_soft=dropped_soft,
    )


def _contains_term(haystack: str, term: str) -> bool:
    """Whole-word/whole-phrase containment, not substring containment.

    `in` would let "remote" match inside "remotely-managed", turning
    an unrelated posting remote. A word-boundary regex is what stops
    that: the match is required to have a non-word character (or the
    string edge) on each side, which also lets a multi-word term like
    "work from home" be matched as one phrase with boundaries only at
    its two ends, not at the internal spaces.
    """
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, haystack) is not None


def infer_work_mode(
    title: str | None,
    location: str | None,
    description: str | None,
) -> str | None:
    """Guess "remote", "hybrid" or None from a job's text fields.

    Never returns "onsite". The description is a 500-character
    excerpt (see Day 6), so the absence of the word "remote" is not
    evidence a role is onsite -- it may simply be in the part of the
    posting that got cut off. "onsite" would be a guess written into
    a column that looks like a fact. None means not determined, and
    scoring_runs.jobs_remote / jobs_hybrid are what make how far this
    rule actually reaches visible, instead of assumed.

    hybrid terms are checked FIRST and win outright. A posting reading
    "hybrid - 3 days remote per week" is a hybrid role, not a remote
    one, and checking remote first would get that backwards -- the
    posting would be marked remote from the word "remote" alone, with
    the word "hybrid" sitting right next to it ignored.
    """
    combined = " ".join(part for part in (title, location, description) if part).lower()
    if not combined:
        return None

    for term in settings.hybrid_term_list:
        if _contains_term(combined, term):
            return "hybrid"

    for term in settings.remote_term_list:
        if _contains_term(combined, term):
            return "remote"

    return None
