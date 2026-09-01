"""Tests for Day 6 job ingestion: pure functions plus the Adzuna client.

No network and no database. The client is exercised through
httpx.MockTransport, which never opens a socket -- that is the whole
reason it is used instead of a live call. pytest-asyncio is not a
project dependency and no other test in this suite uses it, so the
async cases are driven with a plain asyncio.run() inside an ordinary
sync test function rather than adding a new dependency for this file
alone.

The sample record shape mirrors what the live API actually returns,
confirmed by scripts/source_isolate.py against a real account: `id` is
a string, `company`/`location` are nested objects keyed by
`display_name`, `created` is ISO 8601 with a trailing Z, and
`description` is exactly 500 characters ending in an ellipsis.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.db.models.ingestion import IngestionStatus
from app.integrations.adzuna import (
    AdzunaClient,
    JobSourceError,
    JobSourceQuotaError,
    SearchPage,
    _parse_created,
)
from app.services.job_ingestion import (
    IngestionResult,
    _Counters,
    _classify,
    clean_description,
    compute_content_hash,
)
from app.services.locations import normalize_location

FAKE_APP_ID = "probe-id-12345"
FAKE_APP_KEY = "probe-key-67890"


def _adzuna_record(**overrides: Any) -> dict[str, Any]:
    """One realistic Adzuna search result record."""
    record: dict[str, Any] = {
        "id": "4671234567",
        "title": "Data Analyst",
        "company": {"display_name": "Acme Corp"},
        "location": {"display_name": "Gurgaon, Haryana, India"},
        # Adzuna's hard cap, measured: always exactly 500 chars, always
        # ending in an ellipsis.
        "description": "A" * 499 + "…",
        "redirect_url": "https://www.adzuna.in/land/ad/4671234567",
        "created": "2026-08-20T12:35:52Z",
    }
    record.update(overrides)
    return record


def _json_handler(payload: dict[str, Any], status_code: int = 200):
    """A MockTransport handler that always returns the same JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return handler


def _client(handler, app_id: str = "test-app-id", app_key: str = "test-app-key") -> AdzunaClient:
    """An AdzunaClient wired to a MockTransport. Opens no socket."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return AdzunaClient(app_id=app_id, app_key=app_key, client=http_client)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# -- clean_description --------------------------------------------------


def test_clean_description_strips_html_tags() -> None:
    result = clean_description("<p>Hello <b>world</b></p>")
    assert "<" not in result
    assert ">" not in result


def test_clean_description_unescapes_entities() -> None:
    result = clean_description("Sales &amp; Marketing")
    assert "&" in result
    assert "&amp;" not in result


def test_clean_description_removes_trailing_ellipsis_char() -> None:
    result = clean_description("A great role in analytics…")
    assert not result.endswith("…")


def test_clean_description_removes_trailing_triple_dot() -> None:
    result = clean_description("A great role in analytics...")
    assert not result.endswith("...")


def test_clean_description_collapses_whitespace() -> None:
    result = clean_description("Line one\n\n   Line   two")
    assert "  " not in result


def test_clean_description_none_returns_none() -> None:
    assert clean_description(None) is None


def test_clean_description_empty_string_returns_none() -> None:
    assert clean_description("") is None


# -- normalize_location ---------------------------------------------------


def test_normalize_location_folds_gurugram_to_gurgaon() -> None:
    assert normalize_location("Gurugram") == "gurgaon"


def test_normalize_location_folds_bangalore_to_bengaluru() -> None:
    assert normalize_location("Bangalore") == "bengaluru"


def test_normalize_location_strips_state_and_country_tail() -> None:
    assert normalize_location("Bengaluru, Karnataka, India") == "bengaluru"


def test_normalize_location_none_returns_empty_string() -> None:
    assert normalize_location(None) == ""


# -- compute_content_hash --------------------------------------------------


def test_content_hash_is_stable_across_calls() -> None:
    first = compute_content_hash("Data Analyst", "Acme", "Gurgaon")
    second = compute_content_hash("Data Analyst", "Acme", "Gurgaon")
    assert first == second


def test_content_hash_folds_gurgaon_and_gurugram() -> None:
    a = compute_content_hash("Data Analyst", "Acme", "Gurgaon")
    b = compute_content_hash("Data Analyst", "Acme", "Gurugram")
    assert a == b


def test_content_hash_differs_on_company() -> None:
    a = compute_content_hash("Data Analyst", "Acme", "Gurgaon")
    b = compute_content_hash("Data Analyst", "Widgets Inc", "Gurgaon")
    assert a != b


def test_content_hash_ignores_case_and_spacing_in_title() -> None:
    a = compute_content_hash("Data   Analyst", "Acme", "Gurgaon")
    b = compute_content_hash("data analyst", "Acme", "Gurgaon")
    assert a == b


# -- _parse_created ---------------------------------------------------------


def test_parse_created_with_trailing_z_is_aware_utc() -> None:
    parsed = _parse_created("2026-08-20T12:35:52Z")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(0)


def test_parse_created_naive_string_assumes_utc() -> None:
    parsed = _parse_created("2026-08-20T12:35:52")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_created_rubbish_returns_none() -> None:
    assert _parse_created("not a date at all") is None


def test_parse_created_none_returns_none() -> None:
    assert _parse_created(None) is None


# -- AdzunaClient.search: parsing -------------------------------------------


def test_client_parses_a_good_page() -> None:
    handler = _json_handler({"count": 1, "results": [_adzuna_record()]})

    async def scenario() -> SearchPage:
        return await _client(handler).search(what="data analyst", where="Gurgaon")

    page = _run(scenario())
    assert len(page.postings) == 1
    assert len(page.unparseable) == 0


def test_client_keeps_good_records_when_one_is_malformed() -> None:
    malformed = {
        "company": {"display_name": "Foo"},
        "location": {"display_name": "Bar"},
        "description": "not much here",
        "redirect_url": "",
        "created": None,
    }
    handler = _json_handler(
        {"count": 2, "results": [_adzuna_record(), malformed]}
    )

    async def scenario() -> SearchPage:
        return await _client(handler).search()

    page = _run(scenario())
    assert len(page.postings) == 1
    assert len(page.unparseable) == 1


def test_client_maps_nested_company_and_location() -> None:
    record = _adzuna_record(
        company={"display_name": "Widget Inc"},
        location={"display_name": "Pune, Maharashtra, India"},
    )
    handler = _json_handler({"count": 1, "results": [record]})

    async def scenario() -> SearchPage:
        return await _client(handler).search()

    page = _run(scenario())
    assert page.postings[0].company == "Widget Inc"
    assert page.postings[0].location == "Pune, Maharashtra, India"


# -- AdzunaClient.search: errors ---------------------------------------------


def test_client_raises_quota_error_on_429() -> None:
    handler = _json_handler({"error": "too many requests"}, status_code=429)

    async def scenario() -> None:
        await _client(handler).search()

    with pytest.raises(JobSourceQuotaError):
        _run(scenario())


def test_client_raises_source_error_on_500_not_quota_error() -> None:
    handler = _json_handler({"error": "internal"}, status_code=500)

    async def scenario() -> None:
        await _client(handler).search()

    with pytest.raises(JobSourceError) as excinfo:
        _run(scenario())
    assert not isinstance(excinfo.value, JobSourceQuotaError)


def test_quota_error_message_has_no_credentials() -> None:
    handler = _json_handler({"error": "too many requests"}, status_code=429)

    async def scenario() -> None:
        await _client(handler, app_id=FAKE_APP_ID, app_key=FAKE_APP_KEY).search()

    with pytest.raises(JobSourceQuotaError) as excinfo:
        _run(scenario())

    message = str(excinfo.value)
    assert FAKE_APP_ID not in message
    assert FAKE_APP_KEY not in message
    assert "app_id=" not in message


def test_server_error_message_has_no_credentials() -> None:
    handler = _json_handler({"error": "internal"}, status_code=500)

    async def scenario() -> None:
        await _client(handler, app_id=FAKE_APP_ID, app_key=FAKE_APP_KEY).search()

    with pytest.raises(JobSourceError) as excinfo:
        _run(scenario())

    message = str(excinfo.value)
    assert FAKE_APP_ID not in message
    assert FAKE_APP_KEY not in message
    assert "app_id=" not in message


def test_client_raises_source_error_on_non_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    async def scenario() -> None:
        await _client(handler).search()

    with pytest.raises(JobSourceError):
        _run(scenario())


def test_client_raises_source_error_when_results_missing() -> None:
    handler = _json_handler({"count": 0})

    async def scenario() -> None:
        await _client(handler).search()

    with pytest.raises(JobSourceError):
        _run(scenario())


def test_client_handles_empty_results_list() -> None:
    handler = _json_handler({"count": 0, "results": []})

    async def scenario() -> SearchPage:
        return await _client(handler).search()

    page = _run(scenario())
    assert page.postings == []
    assert page.unparseable == []


# -- AdzunaClient.search: the `what` parameter -------------------------------


def test_empty_what_omits_the_parameter() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"count": 0, "results": []})

    async def scenario() -> None:
        await _client(handler).search(what="", where="Gurgaon")

    _run(scenario())
    assert "what" not in captured[-1].url.params


def test_nonempty_what_sends_the_parameter() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"count": 0, "results": []})

    async def scenario() -> None:
        await _client(handler).search(what="python", where="Gurgaon")

    _run(scenario())
    assert captured[-1].url.params["what"] == "python"


# -- _classify ----------------------------------------------------------


def test_classify_zero_fetched_is_no_results() -> None:
    counters = _Counters(records_fetched=0)
    assert _classify(counters) is IngestionStatus.NO_RESULTS


def test_classify_fetched_but_nothing_survived_is_all_rejected() -> None:
    counters = _Counters(records_fetched=5, normalize_failed=3, validation_failed=2)
    assert _classify(counters) is IngestionStatus.ALL_REJECTED


def test_classify_only_duplicates_is_complete() -> None:
    """The steady state -- almost everything is a repost most days -- and
    it must never be mistaken for a run that produced nothing."""
    counters = _Counters(records_fetched=5, duplicates=5)
    assert _classify(counters) is IngestionStatus.COMPLETE


def test_classify_with_insertions_is_complete() -> None:
    counters = _Counters(records_fetched=5, inserted=5)
    assert _classify(counters) is IngestionStatus.COMPLETE


# -- _Counters ------------------------------------------------------------


def test_counters_accounted_for_excludes_non_terminal_fields() -> None:
    counters = _Counters(
        queries_attempted=9,
        pages_fetched=9,
        records_fetched=10,
        normalize_failed=1,
        validation_failed=1,
        filtered_out=1,
        duplicates=1,
        inserted=1,
        retired=9,
    )
    assert counters.accounted_for() == 5


# -- IngestionResult.is_healthy -----------------------------------------


@pytest.mark.parametrize("status", list(IngestionStatus))
def test_is_healthy_true_only_for_complete(status: IngestionStatus) -> None:
    result = IngestionResult(run_id=1, status=status, counters={})
    assert result.is_healthy == (status is IngestionStatus.COMPLETE)
