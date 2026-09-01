"""Adzuna job search: a query in, RawJobPosting objects out.

The only file in the codebase that knows Adzuna's field names, its
URL shape, or that it authenticates with a pair of values rather than
a token. Everything above this talks to SearchPage and RawJobPosting.

Two facts about this provider drive most of what follows, and both
were measured against the live API rather than read in documentation:

1. Descriptions are hard-capped at 500 characters. Every one of 27
   postings sampled across three different queries was exactly 500
   characters and ended in an ellipsis. These are excerpts. Nothing
   in this file can fix that; it is recorded here so the next person
   to wonder why job text is short finds the answer at the boundary
   where it enters the system.

2. `what` ANDs its words. "machine learning engineer" returned 3
   results where "machine learning" returned 23 for the same city and
   window. An empty `what` is therefore not a degenerate case to be
   guarded against -- it is the useful default, and this client omits
   the parameter entirely rather than sending an empty string.

NOTHING IN THIS FILE MAY LOG OR RAISE A URL. app_id and app_key are
query parameters, so the URL is a credential. See
app/integrations/http_errors.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.http_errors import describe_http_error
from app.schemas.job import RawJobPosting

logger = logging.getLogger(__name__)

SOURCE_NAME = "adzuna"
BASE_URL = "https://api.adzuna.com/v1/api/jobs"
DEFAULT_TIMEOUT_SECONDS = 30.0


class JobSourceError(Exception):
    """The source could not be reached, or answered with something unusable."""


class JobSourceQuotaError(JobSourceError):
    """The source refused because this account's quota is spent.

    A distinct type, not a flag on JobSourceError, because the correct
    reaction is different in a way that matters. A transient failure
    invites a retry; an exhausted quota does not, and retrying it
    spends the little that remains while making the real cause harder
    to see. This is the same lesson app/integrations/gemini.py encodes
    by excluding 429 from its retry list: a quota error that gets
    retried surfaces as a timeout, and a timeout sends debugging after
    the network instead of the account.

    It matters more here than there. Adzuna's free tier is roughly
    1,000 calls per MONTH. An hourly quota forgives a debugging loop;
    a monthly one does not.
    """


@dataclass(frozen=True)
class UnparseableRecord:
    """A record the provider returned that could not be read as a job.

    Carried rather than dropped so the ingestion service can store it
    with a reason. When a provider changes its schema, this payload is
    the only evidence of what changed -- and a run that silently
    discarded it would report zero new jobs with nothing to explain
    why.
    """

    raw: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class SearchPage:
    """One page of results, with the failures kept alongside the successes.

    Returning only the successes would make a page where every record
    failed to parse indistinguishable from a page that was genuinely
    empty. Those two have opposite causes -- one is our bug, one is
    the state of the job market -- so they are counted separately all
    the way up.
    """

    total_available: int
    postings: list[RawJobPosting]
    unparseable: list[UnparseableRecord]

    @property
    def records_returned(self) -> int:
        """How many records the provider actually sent, parsed or not."""
        return len(self.postings) + len(self.unparseable)


class AdzunaClient:
    """One configured Adzuna account, queried for job postings."""

    def __init__(
        self,
        app_id: str | None = None,
        app_key: str | None = None,
        country: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        `client` is injectable so tests can pass an httpx.AsyncClient
        built on a MockTransport and exercise this class with no
        network at all -- the same reasoning as extract_cv accepting a
        gemini_client, and as CVIntakeService accepting a storage_dir.
        A test that has to monkeypatch module internals to run is a
        test that will break when the internals move.
        """
        self._app_id = app_id if app_id is not None else settings.adzuna_app_id
        self._app_key = app_key if app_key is not None else settings.adzuna_app_key
        self._country = country or settings.adzuna_country
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    @property
    def source_name(self) -> str:
        return SOURCE_NAME

    def credentials_present(self) -> bool:
        """True when both values are configured. Never reveals either."""
        return bool(self._app_id) and bool(self._app_key)

    async def __aenter__(self) -> AdzunaClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        *,
        what: str = "",
        where: str = "",
        page: int = 1,
        max_days_old: int | None = None,
        results_per_page: int | None = None,
    ) -> SearchPage:
        """Fetch one page of results.

        Raises JobSourceQuotaError on 429 and JobSourceError on any
        other failure. Never raises anything carrying a URL.
        """
        if self._client is None:
            raise JobSourceError(
                "AdzunaClient used outside an `async with` block and with no "
                "injected client"
            )

        if not self.credentials_present():
            raise JobSourceError("Adzuna credentials are not configured")

        params: dict[str, Any] = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "results_per_page": results_per_page or settings.adzuna_results_per_page,
            "max_days_old": (
                max_days_old if max_days_old is not None else settings.adzuna_max_days_old
            ),
            "sort_by": settings.adzuna_sort_by,
        }

        # Omitted entirely rather than sent empty. An empty `what` is
        # the default and it means "no keyword filter"; sending
        # what="" would be a filter for the empty string.
        if what:
            params["what"] = what
        if where:
            params["where"] = where

        url = f"{BASE_URL}/{self._country}/search/{page}"

        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            safe = describe_http_error(error, timeout_seconds=self._timeout_seconds)
            if error.response.status_code == 429:
                raise JobSourceQuotaError(safe) from None
            # `from None` rather than `from error` on purpose: a
            # chained traceback prints the original exception, and the
            # original exception's string form contains the URL and
            # therefore both credentials. Suppressing the chain is
            # what keeps them out of a crash dump.
            raise JobSourceError(safe) from None
        except Exception as error:  # noqa: BLE001 - provider errors are not typed
            raise JobSourceError(
                describe_http_error(error, timeout_seconds=self._timeout_seconds)
            ) from None

        try:
            payload = response.json()
        except ValueError:
            raise JobSourceError("response body was not valid JSON") from None

        if not isinstance(payload, dict):
            raise JobSourceError(
                f"expected a JSON object, got {type(payload).__name__}"
            )

        return self._parse_page(payload)

    def _parse_page(self, payload: dict[str, Any]) -> SearchPage:
        """Turn Adzuna's response body into a SearchPage.

        Every record is attempted independently. One malformed posting
        must not discard the forty-nine good ones sharing its page --
        that would turn a provider's single bad row into a whole run
        producing nothing, and it would look like an outage.
        """
        results = payload.get("results")
        if not isinstance(results, list):
            raise JobSourceError("response contained no 'results' list")

        postings: list[RawJobPosting] = []
        unparseable: list[UnparseableRecord] = []

        for record in results:
            if not isinstance(record, dict):
                unparseable.append(
                    UnparseableRecord(
                        raw={"value": repr(record)[:500]},
                        reason=f"record was {type(record).__name__}, not an object",
                    )
                )
                continue

            try:
                postings.append(self._to_posting(record))
            except Exception as error:  # noqa: BLE001 - any parse failure is data
                unparseable.append(
                    UnparseableRecord(raw=record, reason=str(error)[:500])
                )

        total = payload.get("count")
        return SearchPage(
            total_available=total if isinstance(total, int) else 0,
            postings=postings,
            unparseable=unparseable,
        )

    def _to_posting(self, record: dict[str, Any]) -> RawJobPosting:
        """Map one Adzuna record onto RawJobPosting.

        Adzuna nests company and location one level down, under a
        `display_name` key. Reading those defensively rather than with
        record["company"]["display_name"] is not paranoia: the field
        coverage probe found them present in every sampled record, but
        a sample of 27 is not a guarantee, and a KeyError here would
        cost the whole page.
        """
        return RawJobPosting(
            source=SOURCE_NAME,
            external_id=str(record.get("id") or "").strip(),
            title=str(record.get("title") or "").strip(),
            url=str(record.get("redirect_url") or "").strip(),
            company=_nested_display_name(record, "company"),
            location=_nested_display_name(record, "location"),
            description=(record.get("description") or None),
            posted_at=_parse_created(record.get("created")),
            # Adzuna has no remote flag. Inferring it from the location
            # or the description text is a rule, not a field, so it
            # belongs in the service if it is ever wanted -- not
            # invented here where it would look like provider data.
            is_remote=False,
        )


def _nested_display_name(record: dict[str, Any], key: str) -> str | None:
    """Read record[key]['display_name'] without assuming either level exists."""
    node = record.get(key)
    if not isinstance(node, dict):
        return None
    value = node.get("display_name")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:255]


def _parse_created(value: Any) -> datetime | None:
    """Parse Adzuna's `created` into an aware UTC datetime, or None.

    Adzuna sends ISO 8601 with a trailing Z -- '2026-08-20T12:35:52Z',
    confirmed against the live API. fromisoformat() handles the Z
    suffix from Python 3.11 onward, but the aware-ness is asserted
    afterwards rather than assumed, because a naive datetime does not
    fail loudly. It compares fine against another naive datetime, and
    is simply wrong by this machine's UTC offset -- invisible at a
    14-day freshness window and wrong at a 1-day one.

    Returning None for an unparseable date rather than raising is
    deliberate: posted_at is nullable, a posting with an unreadable
    date is still a real job, and the freshness filter treats an
    unknown date as its own case rather than as old or new.
    """
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None

    if parsed.tzinfo is None:
        # No offset in the string. Assuming UTC is a guess, so it is
        # made explicitly and in one place rather than left implicit.
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)
