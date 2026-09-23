"""score_and_notify_user's own call graph cannot reach Gemini. Structurally.

Stage 0's question 8 asked whether the preferences-triggered path spends
a Gemini call, ever -- even in the rare case where a COMPLETE user's CV
somehow is not yet embedded. The answer designed into score_and_notify_
user is that it never tries: it calls only run_scoring (job_scoring.py)
and run_notification_delivery (notification_delivery.py itself), neither
of which imports a Gemini client, and it deliberately does NOT call
embed_cv_version -- see score_and_notify_user's own docstring.

A docstring saying that is not a check -- CLAUDE.md section 0. This
proves it the same way tests/test_preferences_service_isolation.py
proves PreferencesService cannot reach a CV: by parsing the two modules'
own import statements with ast and failing if either names a Gemini
integration module. It does not walk the transitive closure (neither
file needs to import anything that itself imports Gemini for this
property to hold, and neither currently does), matching that test's own
documented limits.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# score_and_notify_user's own two callers, plus itself: everything a
# preferences- or onboarding-triggered instant recommendation actually
# executes downstream of the embed step (which is covered separately --
# embed_cv_version's own zero-Gemini-when-already-embedded property is
# exercised in tests/integration/test_cv_embedding_pipeline.py).
CHECKED_PATHS = (
    Path("app/services/notification_delivery.py"),
    Path("app/services/job_scoring.py"),
)

FORBIDDEN_IMPORT_PREFIXES = (
    "app.integrations.gemini",
    "app.integrations.gemini_embeddings",
    "app.integrations.gemini_enrichment",
)


def _imported_module_names(source: str) -> list[str]:
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return names


def test_notification_delivery_and_job_scoring_import_no_gemini_client() -> None:
    offenders: dict[str, list[str]] = {}

    for relative_path in CHECKED_PATHS:
        path = REPO_ROOT / relative_path
        assert path.is_file(), f"{path} does not exist"

        imported = _imported_module_names(path.read_text(encoding="utf-8"))
        hits = [
            name
            for name in imported
            for banned in FORBIDDEN_IMPORT_PREFIXES
            if name == banned or name.startswith(banned + ".")
        ]
        if hits:
            offenders[str(relative_path)] = hits

    assert not offenders, (
        "score_and_notify_user's call graph imports a Gemini client: "
        f"{offenders}. The preferences-triggered path must spend zero "
        "Gemini calls unconditionally, not just in the common case."
    )
