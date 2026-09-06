"""PreferencesService must be structurally unable to touch CV data.

The command this guards -- editing user_preferences -- must never
delete or trigger anything CV-related: no cvs/cv_versions/profiles
row, no active_cv_version_id, no re-extraction, no re-embedding, and
per the whole point of the command, zero Gemini calls on any path.

A docstring promising that is not a check -- see CLAUDE.md section 0.
This asserts it structurally, the same way test_workflow_graph.py
proves langgraph is imported by exactly one module: parse the
service's own source with ast and fail if it imports anything that
could reach a CV, a profile, or a Gemini/Adzuna client. Absence of an
import is provable; absence of a call someone could add next month
under an existing import is not, which is why app.integrations is
banned wholesale rather than just its two current modules.

Written before app/services/preferences.py exists, so the first run of
this file is a FAILURE, not a skip -- proof the check can fail at all.

WHAT THIS DOES NOT COVER, so the next reader does not over-trust it

The import check parses ONLY preferences.py's own import statements --
it does not walk the transitive closure. app.services.onboarding,
which preferences.py imports three pure symbols from
(parse_list_input, EXPERIENCE_CHOICES, THRESHOLD_CHOICES), itself
imports CVRepository and CVIntakeService for ITS OWN methods
(handle_document, restart). Those names never enter preferences.py's
namespace and nothing in PreferencesService calls back into onboarding
to reach them -- importing three pure functions/constants executes no
CV or Gemini code -- but a reader should know the guarantee is "this
file's own imports are clean," not "nothing this file transitively
imports has ever heard of a CV." A transitive check would also flag
every other service in the process for the same reason app.main does,
which is not the failure this test exists to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_PATH = REPO_ROOT / "app" / "services" / "preferences.py"

# Anything that could reach a CV, a profile, or a vendor client that
# spends quota. Banned by dotted prefix, not by exact match, so a
# submodule added later under any of these is caught too.
FORBIDDEN_IMPORT_PREFIXES = (
    "app.db.repositories.cv",
    "app.db.repositories.profile",
    "app.db.models.cv",
    "app.db.models.profile",
    "app.services.cv_intake",
    "app.services.cv_extraction",
    "app.services.cv_embedding",
    "app.integrations",  # Adzuna and Gemini both live here -- see CLAUDE.md section 4.
)


def _imported_module_names(source: str) -> list[str]:
    """Every dotted module name this source imports, via either import form."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return names


def test_preferences_service_file_exists() -> None:
    assert SERVICE_PATH.is_file(), (
        f"{SERVICE_PATH} does not exist yet -- this is expected to fail "
        "until Stage B writes it."
    )


def test_preferences_service_imports_no_cv_or_profile_or_vendor_modules() -> None:
    if not SERVICE_PATH.is_file():
        raise AssertionError(
            f"{SERVICE_PATH} does not exist yet -- nothing to check. "
            "This test cannot pass until the file exists AND is clean."
        )

    source = SERVICE_PATH.read_text(encoding="utf-8")
    imported = _imported_module_names(source)

    offenders = [
        name
        for name in imported
        for banned in FORBIDDEN_IMPORT_PREFIXES
        if name == banned or name.startswith(banned + ".")
    ]

    assert not offenders, (
        "app/services/preferences.py imports something that could reach "
        f"CV data or a vendor client: {offenders}. Editing a preference "
        "must never touch a CV, a profile, or spend a Gemini call."
    )


def test_preferences_service_constructor_builds_only_a_user_repository() -> None:
    """Not owning a CVRepository is a stronger guarantee than not calling one.

    Complements the import-absence test above: that one proves the
    module cannot construct a CVRepository or ProfileRepository even if
    it wanted to (the class does not exist in this file's namespace).
    This one proves the class does not, in fact, build one at
    __init__ time -- so a reviewer does not have to trust that no
    method quietly imports one locally at call time either.
    """
    from app.services.preferences import PreferencesService

    fake_session = object()  # UserRepository only stores it; never touched at init.
    service = PreferencesService(fake_session)

    type_names = {type(value).__name__ for value in vars(service).values()}
    forbidden = {"CVRepository", "ProfileRepository", "CVIntakeService"}

    assert "UserRepository" in type_names, (
        f"PreferencesService.__init__ built {type_names} -- expected a "
        "UserRepository among them."
    )
    assert not (type_names & forbidden), (
        f"PreferencesService.__init__ built {type_names & forbidden} -- "
        "a CVRepository or ProfileRepository in scope here is exactly "
        "what this command must never have."
    )
