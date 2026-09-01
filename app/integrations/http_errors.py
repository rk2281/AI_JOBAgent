"""Turn an httpx exception into a string that is safe to show a human.

This exists because of one specific fact about one specific provider:
Adzuna authenticates with `app_id` and `app_key` as URL query
parameters rather than headers. httpx formats HTTPStatusError as
"Client error '401 Unauthorized' for url 'https://...app_id=X&app_key=Y'"
-- so the exception's own string form carries both credentials. So do
exc.request.url and response.url.

That makes the ordinary, careful-looking things dangerous:
logger.error("fetch failed: %s", exc) writes both keys to every log
sink, and storing str(exc) in ingestion_runs.error_message writes them
to the database permanently, where they will be read back by a human
weeks later with no idea they are looking at a live credential.

This is the same reasoning as _format_errors() in
app/integrations/gemini.py, which reads only `code` and `message` off
a provider error rather than formatting the object: an error object is
not automatically safe to format just because it is an error.

Lives in app/integrations/ rather than app/utils/ because the reason
it exists is a property of an outbound third-party client, and this is
the layer that owns those.
"""

from __future__ import annotations

import httpx


def describe_http_error(error: Exception, *, timeout_seconds: float | None = None) -> str:
    """Describe an httpx failure using only its status code and type.

    Deliberately returns less information than the exception contains.
    The discarded part is the URL, and the URL is the part that is
    unsafe.
    """
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        return f"HTTP {response.status_code} {response.reason_phrase}"

    if isinstance(error, httpx.TimeoutException):
        if timeout_seconds is not None:
            return f"timed out after {timeout_seconds}s"
        return "timed out"

    if isinstance(error, httpx.TransportError):
        # Connection-level failures. The message can name a host but
        # not the query string; the class name alone is reported to
        # stay on the safe side of that distinction rather than
        # relying on it.
        return f"connection failed ({type(error).__name__})"

    return f"unexpected error ({type(error).__name__})"
