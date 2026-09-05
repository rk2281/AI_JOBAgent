<#
.SYNOPSIS
    Refuse an archive that must not be shared. Read-only.

.DESCRIPTION
    A thin wrapper around scripts/verify_archive.py. The rules live in
    Python, where they are unit-tested (tests/test_verify_archive.py)
    and where a drift test compares them against pack.ps1's own
    $ForbiddenPatterns. This file exists so the muscle memory built by
    pack.ps1 works for checking too:

        powershell -File scripts/pack.ps1
        powershell -File scripts/verify_archive.ps1 C:\Temp\handoff.zip

    WHY A WRAPPER RATHER THAN A SECOND IMPLEMENTATION

    A safety rule written twice is a safety rule that drifts, and the
    copy that drifts is the one nobody runs. There is exactly one list
    of forbidden patterns in executable form, in Python. This file
    forwards arguments and forwards the exit code.

    WHAT IT CHECKS AND WHAT IT DOES NOT

    It answers "would sharing this leak something". It does NOT answer
    "does this archive contain what it should" -- untracked files are
    invisible to git archive, and no inspection of the resulting zip
    can tell you about a file that was never in it. See CLAUDE.md
    section 3.

.PARAMETER ArchivePath
    Path to the .zip to inspect. Never modified.

.PARAMETER SelfTest
    Build one throwaway archive per forbidden pattern, assert each is
    caught, print the results and exit. Run this once before trusting
    the script.

.EXAMPLE
    powershell -File scripts/verify_archive.ps1 -SelfTest

.EXAMPLE
    powershell -File scripts/verify_archive.ps1 C:\Temp\handoff.zip

.NOTES
    Exit codes, forwarded unchanged from the Python:
        0  clean
        1  forbidden entries found -- DO NOT SHARE
        2  could not read the archive at all

    2 is separate from 1 on purpose. "This archive is unsafe" and "I
    could not tell" are different answers.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$ArchivePath,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

# Run from the repository root, because the module is invoked as
# `python -m scripts.verify_archive` and that only resolves from there.
# $PSScriptRoot is scripts/, so the root is its parent -- derived
# rather than assumed, so this works from any working directory.
$repoRoot = Split-Path -Parent $PSScriptRoot

# The venv interpreter if there is one, otherwise whatever `python`
# resolves to. Same reasoning as scripts/schedule_agent.ps1 using an
# absolute interpreter path: a script that silently runs under the
# wrong Python is a script that silently checks nothing.
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$arguments = @("-m", "scripts.verify_archive")

if ($SelfTest) {
    $arguments += "--self-test"
}
elseif ($ArchivePath) {
    $arguments += $ArchivePath
}
else {
    Write-Error "Give a path to an archive, or pass -SelfTest."
    exit 2
}

Push-Location $repoRoot
try {
    & $python @arguments
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $code
