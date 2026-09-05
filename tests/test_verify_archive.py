"""Tests for scripts/verify_archive.py, including drift against pack.ps1.

The rules are duplicated across two languages -- PowerShell in
`scripts/pack.ps1`, Python in `scripts/verify_archive.py` -- and a
safety list written twice is a safety list that drifts. The drift test
at the bottom is the whole reason duplication was acceptable: adding a
pattern in one file and forgetting the other is a test failure here,
not a gap discovered by the next leak.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from scripts.verify_archive import (
    FORBIDDEN_PATTERNS,
    entry_names,
    find_violations,
    main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK_SCRIPT = REPO_ROOT / "scripts" / "pack.ps1"


def _labels(violations) -> set[str]:
    return {violation.label for violation in violations}


# --- what must be refused -------------------------------------------------


def test_a_root_env_file_is_refused() -> None:
    assert _labels(find_violations(["app/main.py", ".env"])) == {".env"}


def test_a_nested_env_file_is_refused() -> None:
    """The shape every real incident here has had.

    `git archive` prefixes entries with a directory, and both leaked
    archives were `AI_JOB_HUNT_AGENT/.env` rather than `.env`. A rule
    matching only the root would have passed both of them.
    """
    assert _labels(find_violations(["AI_JOB_HUNT_AGENT/.env"])) == {".env"}


def test_storage_and_pdfs_are_refused_separately() -> None:
    """One CV file trips two rules, and both are reported.

    They are not redundant: a PDF outside storage/ is still a CV risk,
    and a non-PDF inside storage/ is still private user data.
    """
    names = ["AI_JOB_HUNT_AGENT/storage/cvs/2/a.pdf"]
    assert _labels(find_violations(names)) == {"storage/", "*.pdf"}


def test_a_nested_zip_is_refused() -> None:
    """A zip inside a zip is not inspected, so it cannot be cleared.

    The Day 10 Part 3 incident archive carried a stray `files (1).zip`.
    """
    assert _labels(find_violations(["files (1).zip"])) == {"*.zip"}


def test_git_history_is_refused() -> None:
    assert _labels(find_violations(["AI_JOB_HUNT_AGENT/.git/config"])) == {".git/"}


def test_every_matching_entry_is_reported_not_just_the_first() -> None:
    """"One stray file" and "21 CVs" are different conversations."""
    names = [f"storage/cvs/2/{index}.pdf" for index in range(21)]
    violations = find_violations(names)

    storage = next(v for v in violations if v.label == "storage/")
    assert len(storage.entries) == 21


# --- what must NOT be refused --------------------------------------------


def test_a_clean_repository_passes() -> None:
    names = [
        "AI_JOB_HUNT_AGENT/app/main.py",
        "AI_JOB_HUNT_AGENT/README.md",
        "AI_JOB_HUNT_AGENT/.env.example",
        "AI_JOB_HUNT_AGENT/requirements.txt",
    ]
    assert find_violations(names) == []


def test_env_example_is_not_mistaken_for_env() -> None:
    """The file that EXISTS to be shared must not be blocked.

    A checker that refuses a correct archive gets bypassed, and then
    there is no checker at all. This is the same reasoning as the Day
    10 Part 2 tracing-flag entry: a guard wrong in the case it approves
    is the guard that gets deleted.
    """
    assert find_violations([".env.example", "AI_JOB_HUNT_AGENT/.env.example"]) == []


def test_a_dotenv_suffixed_file_is_not_matched() -> None:
    """`config.env` is a different file from `.env`."""
    assert find_violations(["config.env", "app/settings.env"]) == []


# --- exit codes -----------------------------------------------------------


def test_exit_code_zero_for_a_clean_archive(tmp_path: Path) -> None:
    archive = tmp_path / "clean.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("app/main.py", "x")

    assert main([str(archive)]) == 0


def test_exit_code_one_when_something_is_forbidden(tmp_path: Path) -> None:
    archive = tmp_path / "leaky.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("AI_JOB_HUNT_AGENT/.env", "SECRET=1")

    assert main([str(archive)]) == 1


def test_exit_code_two_when_the_archive_cannot_be_read(tmp_path: Path) -> None:
    """Unreadable is neither cleared nor condemned.

    A wrapper treating any non-zero as unsafe still behaves correctly;
    one treating any non-one as safe does not. That asymmetry is the
    reason for a third code.
    """
    missing = tmp_path / "nope.zip"
    assert main([str(missing)]) == 2

    not_a_zip = tmp_path / "notazip.zip"
    not_a_zip.write_bytes(b"this is not a zip file")
    assert main([str(not_a_zip)]) == 2


def test_the_verifier_reads_a_real_zip_end_to_end(tmp_path: Path) -> None:
    archive = tmp_path / "mixed.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("AI_JOB_HUNT_AGENT/app/main.py", "x")
        handle.writestr("AI_JOB_HUNT_AGENT/storage/cvs/2/a.pdf", "x")

    violations = find_violations(entry_names(archive))
    assert _labels(violations) == {"storage/", "*.pdf"}


# --- drift ----------------------------------------------------------------


def test_the_pattern_labels_match_pack_ps1() -> None:
    """The two lists must name the same things.

    Parses the `Label = "..."` entries out of pack.ps1's
    $ForbiddenPatterns block. It compares NAMES, not behaviour -- a
    PowerShell predicate cannot be executed from here -- so this
    catches an added or removed rule, which is the realistic drift,
    and not a changed matcher, which is not.

    If this fails, do not edit the expected set. Decide which file is
    right and change the other one.
    """
    assert PACK_SCRIPT.exists(), "scripts/pack.ps1 is missing"

    source = PACK_SCRIPT.read_text(encoding="utf-8", errors="replace")
    block = source.split("$ForbiddenPatterns = @(", 1)
    assert len(block) == 2, "Could not find $ForbiddenPatterns in pack.ps1"

    body = block[1].split("\n)", 1)[0]
    powershell_labels = set(re.findall(r'Label\s*=\s*"([^"]+)"', body))

    python_labels = {pattern.label for pattern in FORBIDDEN_PATTERNS}

    assert powershell_labels == python_labels, (
        "scripts/pack.ps1 and scripts/verify_archive.py disagree about "
        f"what must never be shared.\n"
        f"  only in pack.ps1:          {sorted(powershell_labels - python_labels)}\n"
        f"  only in verify_archive.py: {sorted(python_labels - powershell_labels)}"
    )


def test_logs_are_deliberately_absent_from_both_lists() -> None:
    """`logs/` is gitignored but NOT forbidden, and that is a gap.

    The archive this Day 12 work arrived in contained `logs/`. Neither
    list blocks it, so neither script would have complained. That is a
    policy decision nobody has made -- an unattended run log is the one
    file nobody reviews before packing, and it may contain user ids,
    job titles and error text.

    This test pins the CURRENT state rather than fixing it, so that
    adding `logs/` to the policy is a deliberate change to two files
    and this test, and not something that drifts in unnoticed.
    See docs/MVP_LIMITATIONS.md.
    """
    assert find_violations(["logs/run_log.txt", "AI_JOB_HUNT_AGENT/logs/a.log"]) == []
