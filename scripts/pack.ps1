<#
.SYNOPSIS
    Build a shareable archive of this repository using `git archive` only.

.DESCRIPTION
    Nine credential leaks so far all came from a hand-made zip -- someone
    reached for Explorer's "Compress to zip" or `Compress-Archive -Path .`
    and forgot that neither one has ever heard of .gitignore. This script
    exists so the archive that gets shared is never hand-made again.

    `git archive` builds the zip from a committed tree, not from the
    filesystem. .env, storage/, *.zip and everything else in .gitignore
    are excluded BY CONSTRUCTION -- they were never tracked, so they were
    never in the tree, so there is nothing to remember to exclude. .git/
    itself is excluded the same way: `git archive` only ever emits the
    tracked file content at a ref, never the object database.

    That guarantee only holds for what is actually committed. A dirty
    working tree means uncommitted changes exist that `git archive` will
    silently leave out of the package -- not a credential risk, but a
    "the archive doesn't contain what's on disk" risk, which is its own
    way of shipping the wrong thing. So this script refuses to run
    against a dirty tree unless -Force says the caller has already
    decided that's fine.

    The assertion after packing is the actual point of this script. A
    packer that packs correctly today and has nothing checking its own
    output is exactly one refactor away from becoming the tenth leak.

.PARAMETER OutputPath
    Where to write the archive. Must resolve to a path OUTSIDE this
    repository -- writing the archive back inside the repo it was just
    built from is how it ends up tracked, or zipped into the next
    archive, or missed by a naive "just email the repo folder" copy.
    Defaults to a timestamped .zip in $env:TEMP.

.PARAMETER Ref
    The git ref to archive. Defaults to HEAD.

.PARAMETER Force
    Skip the dirty-working-tree check. Does NOT skip the post-pack
    content assertion -- that check never has an opt-out.

.PARAMETER SelfTest
    Build a throwaway archive in $env:TEMP, run the same assertions
    against it, print the result, and delete it. Bypasses the dirty
    check on purpose: this mode exists to prove the packer and the
    assertions work, not to produce a real deliverable, and this
    repository is expected to have uncommitted work in progress most of
    the time. Run this before ever running the script for real.

.EXAMPLE
    powershell -File scripts/pack.ps1 -SelfTest

.EXAMPLE
    powershell -File scripts/pack.ps1

.EXAMPLE
    powershell -File scripts/pack.ps1 -OutputPath C:\Temp\handoff.zip -Force
#>

[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$Ref = "HEAD",
    [switch]$Force,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

# Entries that must never appear in the archive, no matter how they got
# there. .env and *.pdf catch a credential or a real CV slipping in
# through a tracking mistake; .git/ and storage/ and *.zip catch this
# script itself being pointed at the wrong tree or the wrong ref. Every
# one of these SHOULD already be impossible via .gitignore -- this list
# is what turns "should be impossible" into "checked every single run".
$ForbiddenPatterns = @(
    @{ Label = ".env";     Test = { param($Name) $Name -eq ".env" -or $Name.EndsWith("/.env") } }
    @{ Label = ".git/";    Test = { param($Name) $Name -like ".git/*" -or $Name -like "*/.git/*" } }
    @{ Label = "storage/"; Test = { param($Name) $Name -like "storage/*" -or $Name -like "*/storage/*" } }
    @{ Label = "*.zip";    Test = { param($Name) $Name.EndsWith(".zip") } }
    @{ Label = "*.pdf";    Test = { param($Name) $Name.EndsWith(".pdf") } }
)

function Get-RepoRoot {
    $root = git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) {
        throw "Not inside a git repository (git rev-parse --show-toplevel failed)."
    }
    # git always reports forward slashes; normalize for Windows path comparisons.
    return ($root.Trim() -replace '/', '\')
}

function Test-WorkingTreeDirty([string]$RepoRoot) {
    $status = git -C $RepoRoot status --porcelain
    return -not [string]::IsNullOrWhiteSpace($status)
}

function New-PackArchive([string]$RepoRoot, [string]$Path, [string]$GitRef) {
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path $Path) {
        Remove-Item $Path -Force
    }

    # git archive and nothing else. No Compress-Archive, no manual file
    # list, no exclude flags to keep in sync with .gitignore by hand --
    # the tree object IS the exclude list.
    git -C $RepoRoot archive --format=zip --output $Path $GitRef
    if ($LASTEXITCODE -ne 0) {
        throw "git archive exited with code $LASTEXITCODE for ref '$GitRef'."
    }
    if (-not (Test-Path $Path)) {
        throw "git archive reported success but produced no file at $Path."
    }
}

function Assert-ArchiveContents([string]$Path) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName })
        $fileCount = @($zip.Entries | Where-Object { -not $_.FullName.EndsWith('/') }).Count
    }
    finally {
        $zip.Dispose()
    }

    $violations = @()
    foreach ($pattern in $ForbiddenPatterns) {
        $hits = @($names | Where-Object { & $pattern.Test $_ })
        if ($hits.Count -gt 0) {
            $violations += "  $($pattern.Label): $($hits -join ', ')"
        }
    }

    if ($violations.Count -gt 0) {
        $detail = $violations -join [Environment]::NewLine
        throw "Archive at $Path contains forbidden entries:`n$detail"
    }

    $byteSize = (Get-Item $Path).Length
    return [PSCustomObject]@{ FileCount = $fileCount; ByteSize = $byteSize }
}

# --- entry point -------------------------------------------------------

$repoRoot = Get-RepoRoot

if ($SelfTest) {
    $tempPath = Join-Path $env:TEMP ("pack_selftest_{0}.zip" -f ([guid]::NewGuid().ToString("N")))
    try {
        New-PackArchive -RepoRoot $repoRoot -Path $tempPath -GitRef $Ref
        $result = Assert-ArchiveContents -Path $tempPath
        Write-Output "self-test result   PASS"
        Write-Output "file count         $($result.FileCount)"
        Write-Output "byte size          $($result.ByteSize)"
    }
    finally {
        if (Test-Path $tempPath) {
            Remove-Item $tempPath -Force
        }
    }
    exit 0
}

if (-not $Force -and (Test-WorkingTreeDirty $repoRoot)) {
    Write-Error "Working tree is dirty. Commit or stash first, or pass -Force to pack anyway (uncommitted changes will NOT be in the archive -- git archive only ever reads committed content)."
    exit 1
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path $env:TEMP ("ai_job_hunt_agent_{0}.zip" -f $timestamp)
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$resolvedRepoRoot = ([System.IO.Path]::GetFullPath($repoRoot)).TrimEnd('\') + '\'

if ($resolvedOutput.StartsWith($resolvedRepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Error "Output path must be outside the repository. Got: $resolvedOutput"
    exit 1
}

New-PackArchive -RepoRoot $repoRoot -Path $resolvedOutput -GitRef $Ref
$result = Assert-ArchiveContents -Path $resolvedOutput

Write-Output "archive            $resolvedOutput"
Write-Output "file count         $($result.FileCount)"
Write-Output "byte size          $($result.ByteSize)"
