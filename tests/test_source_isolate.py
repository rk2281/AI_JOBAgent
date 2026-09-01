"""Tests for the credential-safe error formatting in scripts.source_isolate.

describe_http_error is what stands between an httpx exception and the
terminal. httpx formats HTTPStatusError as "Client error '401
Unauthorized' for url 'https://...app_id=X&app_key=Y'" -- both
credentials are inside the string form of the exception. These tests
assert that neither value survives into the returned message, which is
the whole reason the function exists.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.http_errors import describe_http_error

FAKE_APP_ID = "fake-app-id-abc123"
FAKE_APP_KEY = "fake-app-key-xyz789"
FAKE_URL = (
    "https://api.adzuna.com/v1/api/jobs/in/search/1"
    f"?app_id={FAKE_APP_ID}&app_key={FAKE_APP_KEY}"
)


def _status_error(status_code: int, reason: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", FAKE_URL)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status_code} {reason}' for url '{FAKE_URL}'",
        request=request,
        response=response,
    )


@pytest.mark.parametrize(
    ("status_code", "expected_fragment"),
    [
        (401, "401"),
        (403, "403"),
        (429, "429"),
        (503, "503"),
    ],
)
def test_status_error_reports_the_code(status_code: int, expected_fragment: str) -> None:
    message = describe_http_error(_status_error(status_code, "Whatever"))
    assert expected_fragment in message


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
def test_status_error_never_leaks_credentials(status_code: int) -> None:
    message = describe_http_error(_status_error(status_code, "Whatever"))
    assert FAKE_APP_ID not in message
    assert FAKE_APP_KEY not in message
    assert "app_id" not in message
    assert "app_key" not in message
    assert "api.adzuna.com" not in message


def test_the_raw_exception_really_does_leak() -> None:
    """Guard against the assumption this module is built on going stale.

    If httpx ever stops embedding the URL in the exception's string
    form, describe_http_error becomes unnecessary rather than wrong --
    but discovering that from a failing test is much better than
    quietly keeping a workaround nobody can justify. This test fails
    loudly if the premise changes.
    """
    assert FAKE_APP_KEY in str(_status_error(401, "Unauthorized"))


def test_timeout_is_reported_without_a_url() -> None:
    message = describe_http_error(
        httpx.ConnectTimeout("timed out", request=None),
        timeout_seconds=30.0,
    )
    assert "timed out" in message
    assert "adzuna" not in message.lower()


def test_transport_error_reports_only_the_class_name() -> None:
    message = describe_http_error(httpx.ConnectError("failed", request=None))
    assert "ConnectError" in message
    assert FAKE_APP_KEY not in message


def test_unknown_error_is_still_handled() -> None:
    message = describe_http_error(ValueError("something odd"))
    assert "unexpected error" in message
    assert "ValueError" in message
