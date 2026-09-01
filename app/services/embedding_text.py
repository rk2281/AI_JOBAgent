"""What text represents a job, and what text represents a CV.

This is a rule, not a client detail, which is why it sits in services
rather than in app/integrations/. The client's job is to turn text
into a vector; deciding WHICH text is a decision about matching, and
Day 8 will reuse build_job_document() as the input to job-skill
extraction so that the embedding and the skill extractor are looking
at the same job.

Two things are deliberately LEFT OUT of the job document, against the
suggestion Day 6 recorded:

  location -- Day 8 scores location deterministically at 15% with
    exact rules. Putting it in the vector too means the same fact is
    counted twice, once badly: the identical role in two cities would
    get different vectors, when what the vector is meant to capture is
    what the job IS. It is also inconsistently present -- 23 of the 99
    stored rows have location "India" with no city, so the field
    supplies noise where it supplies anything at all.

  company -- three staffing agencies account for 23 of the 99 rows.
    Embedding the employer's name makes those 23 cluster with each
    other by who posted them rather than by what the work is; the
    vector learns the agency. A company name says almost nothing about
    the job. The case for keeping it is a user who wants product
    companies rather than agencies, and that is a deterministic
    preference filter, not a semantic one.

The CV document leaves out location and total_experience_years for the
same double-counting reason (15% and 20% deterministic signals on Day
8), and education because it mostly contributes institution names,
which cluster the way company names do.

It DOES include skills, which looks inconsistent -- skills are Day 8's
30% signal. The reason is symmetry. A job's 500-character description
names technologies whether we like it or not, so removing skills from
the CV side would leave one side listing them and the other not, and
cosine similarity would then partly measure "these are different kinds
of document" rather than "this person fits this job". That is the
exact failure the RETRIEVAL_QUERY / RETRIEVAL_DOCUMENT split exists to
reduce, and it is not worth reintroducing by hand.

Skills here come from cv_versions.extracted_profile, which holds the
CV's own spellings, NOT from profiles.skills, which holds normalized
catalog keys like 'cpp' and 'ansys fluent'. Catalog keys are a
matching surface for exact set logic; they are worse input to a
language model than the words the CV actually used.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.config import settings

# A CV listing twelve roles would otherwise crowd out everything else
# in the budget. Five is enough to establish what someone does; the
# ones below that are usually early-career roles that describe a
# different person.
MAX_EXPERIENCE_ENTRIES = 5

# One verbose role description must not consume the whole document.
MAX_ROLE_DESCRIPTION_CHARS = 300


def _clean(value: Any) -> str:
    """Collapse whitespace and coerce to str, tolerating None and non-strings.

    extracted_profile is JSONB written from a language model's output.
    It is validated as a CVProfile at extraction time, but this
    function reads the stored dict rather than the model, and a row
    written before a schema change is not guaranteed to match today's
    shape. Returning "" for anything unusable is what stops one odd
    row from failing an entire embedding pass.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.split())


def _clean_list(value: Any) -> list[str]:
    """The list counterpart of _clean. Drops empties, preserves order."""
    if not isinstance(value, list):
        return []
    cleaned = [_clean(item) for item in value]
    return [item for item in cleaned if item]


def build_job_document(title: str | None, description: str | None) -> str:
    """The canonical text representing one job.

    Labelled rather than concatenated. The labels cost a handful of
    tokens and give the embedding model an explicit boundary between
    the two fields, which matters more than usual here because the
    description is a 500-character excerpt rather than a full posting
    -- there is little context for the model to infer structure from.

    Returns "" when there is nothing to embed. The caller must treat
    that as a skip rather than sending it: an empty string is not a
    failure of the provider, it is a gap in our own data, and the two
    should not be counted together.
    """
    title_text = _clean(title)
    description_text = _clean(description)

    sections: list[str] = []
    if title_text:
        sections.append(f"Job title: {title_text}")
    if description_text:
        sections.append(f"Description: {description_text}")

    return "\n\n".join(sections)


def build_cv_document(extracted_profile: dict[str, Any] | None) -> str:
    """The canonical text representing one CV version.

    Shaped to sit alongside build_job_document's output rather than to
    be a complete rendering of the CV. Everything here answers "what
    kind of work is this person for", which is the only question a job
    vector can answer back.
    """
    if not isinstance(extracted_profile, dict):
        return ""

    sections: list[str] = []

    current_title = _clean(extracted_profile.get("current_title"))
    if current_title:
        sections.append(f"Current role: {current_title}")

    target_roles = _clean_list(extracted_profile.get("target_roles"))
    if target_roles:
        sections.append("Target roles: " + ", ".join(target_roles))

    skills = _clean_list(extracted_profile.get("skills"))
    if skills:
        sections.append("Skills: " + ", ".join(skills))

    summary = _clean(extracted_profile.get("summary"))
    if summary:
        sections.append(f"Summary: {summary}")

    entries = extracted_profile.get("experience")
    if isinstance(entries, list):
        rendered: list[str] = []
        # Taken in stored order and capped, NOT sorted by date. The
        # date fields are frequently None -- that is why
        # total_experience_years is nullable -- so sorting would
        # reorder unpredictably on exactly the CVs where it matters.
        # CVs conventionally list the most recent role first, and
        # relying on that convention is a stated assumption rather
        # than a silent one.
        for entry in entries[:MAX_EXPERIENCE_ENTRIES]:
            if not isinstance(entry, dict):
                continue

            role = _clean(entry.get("title"))
            company = _clean(entry.get("company"))
            role_description = _clean(entry.get("description"))[
                :MAX_ROLE_DESCRIPTION_CHARS
            ]

            if not role and not role_description:
                continue

            heading = f"{role} at {company}" if role and company else (role or company)
            line = f"- {heading}" if heading else "-"
            if role_description:
                line = f"{line}: {role_description}" if heading else f"- {role_description}"
            rendered.append(line)

        if rendered:
            sections.append("Experience:\n" + "\n".join(rendered))

    return "\n\n".join(sections)


def fit_to_budget(text: str, max_chars: int | None = None) -> tuple[str, bool]:
    """Trim to the configured character budget, and say whether it trimmed.

    Applied by us rather than left to the provider. The SDK's
    EmbedContentConfig has an `auto_truncate` field whose default is
    not documented here, and provider-side truncation is the worst
    shape of failure available on this path: the call succeeds, a
    vector comes back, it looks entirely normal, and it describes half
    a CV. Doing it ourselves means it is a counted event.

    Note the comparison. `len(text) > max_chars` -- a document of
    exactly max_chars is NOT truncated, because it fits. Writing this
    as >= would trim one character off every document that lands
    exactly on the boundary and report a truncation that did not need
    to happen.
    """
    limit = settings.embedding_max_chars if max_chars is None else max_chars

    if len(text) <= limit:
        return text, False

    cut = text[:limit]

    # Prefer a word boundary, but only a nearby one. Searching the
    # whole string for a space would let a document with no spaces in
    # its last few thousand characters lose most of its content.
    last_space = cut.rfind(" ")
    if last_space >= limit - 100:
        cut = cut[:last_space]

    return cut.rstrip(), True


def document_hash(text: str) -> str:
    """Stable identity for a piece of embedded text.

    Hash the FINAL text -- after fit_to_budget, not before -- so that
    what is stored in embedding_source_hash is what was actually sent
    to the provider. Hashing the pre-truncation text would make a
    document whose truncation point moved look unchanged.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
