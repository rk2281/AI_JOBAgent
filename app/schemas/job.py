"""The shape every job source must produce, whatever its own API looks like.

This is the seam. app/integrations/adzuna.py knows Adzuna's field
names; everything above it knows only this class. Adding a second
source later means a new file in app/integrations/ that returns
RawJobPosting objects, and no change at all to the ingestion service,
the repository, or the matching code -- which is the entire reason the
seam is a schema rather than a provider object handed upward.

Two stages of rejection meet here, and keeping them apart is what
makes a failed run diagnosable:

  Pydantic validation on this class = "the source sent something we
  could not read at all". Counted as normalize_failed.

  The service's own rules afterwards = "we read it fine, but it is not
  a job we can use". Counted as validation_failed.

Collapsing those into one number would leave you unable to tell a
provider schema change from your own filter being too strict, which
are diagnosed in completely different places.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawJobPosting(BaseModel):
    """One job posting, normalized out of a provider's response shape.

    Required fields are required because the database requires them:
    jobs.external_id, jobs.title and jobs.url are all NOT NULL, and
    external_id is half of a unique constraint. A posting missing any
    of them cannot be stored at all, so it is rejected here rather
    than failing at the INSERT with a less informative error.

    Everything else is optional because the real data says so. Company
    and location are usually present but not guaranteed, and a
    posting with a known title, URL and date is still worth having.
    """

    source: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=255)

    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=1024)

    company: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)

    description: str | None = None

    # Timezone-aware, always. Adzuna returns ISO 8601 with a trailing
    # Z, confirmed against the live API ('2026-08-20T12:35:52Z'), and
    # jobs.posted_at is DateTime(timezone=True). A naive datetime here
    # would not raise -- it would sit 5h30m from the truth on this
    # machine, which is invisible at a 14-day freshness window and
    # wrong at a 1-day one. Converting at the integration boundary is
    # the only place that can be enforced.
    posted_at: datetime | None = None

    is_remote: bool = False
