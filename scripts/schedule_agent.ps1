<#
.SYNOPSIS
    Register (or self-test) the nightly workflow run as a Windows
    Scheduled Task.

.DESCRIPTION
    scripts/run_agent.py already runs the whole graph and already exits
    non-zero when something went wrong. What it does not do is run when
    nobody types the command, and that is all this script adds.

    THE THREE THINGS THAT ACTUALLY GO WRONG WITH SCHEDULED PYTHON

    1. The wrong interpreter. A scheduled task inherits the SYSTEM PATH,
       not the shell you tested in, so `python` resolves to whatever is
       installed machine-wide. This is not hypothetical here: partway
       through Day 10 Part 3 the session's own `python` silently became
       C:\Python312\python.exe and every third-party import in the test
       suite vanished at once -- 22 collection errors that looked like a
       broken repository and were a broken PATH. So the task invokes
       .venv\Scripts\python.exe by absolute path and never the name.

    2. The wrong working directory. Scheduled tasks start in
       C:\Windows\System32 unless told otherwise, and cv_storage_dir is
       "storage/cvs" -- a RELATIVE path resolved against the process
       working directory. A run started in System32 would resolve CV
       storage to C:\Windows\System32\storage\cvs. Hence -WorkingDirectory.

    3. The exit code going nowhere. An unattended run whose result is
       not recorded is a run that reports nothing, which is worse than
       not running: it looks like it worked. run_agent.py returns 1 on
       `degraded`, and that number is the whole point of the wrapper
       below -- it captures stdout, stderr AND $LASTEXITCODE, and writes
       the exit code as the last line of the log.

    NO --dry-run. A scheduled dry run is a scheduled no-op that reports
    a healthy status every morning while doing nothing -- section 0 in
    its purest form. The task runs for real.

    WHAT CAN END UP IN THE LOG

    Checked before writing a line, because CLAUDE.md section 3 records
    that nine of eleven incidents came from a secret handled
    incidentally while doing something else, and an unattended log is
    exactly that shape. The run invokes app/integrations/adzuna.py, and
    Adzuna's app_id and app_key travel as QUERY PARAMETERS, so any
    httpx exception whose text contains the request URL contains both
    credentials.

    It does not reach the log, and that is by construction rather than
    by luck: AdzunaClient passes every provider error through
    describe_http_error() and raises `from None` specifically so a
    chained traceback cannot print the original URL, and the ingestion
    node stores that already-redacted string rather than formatting an
    exception of its own. What the log can therefore contain on a
    failing run is a redacted provider message, a Python traceback whose
    frames are this repository's own files, and the run summary -- which
    prints counters and status strings only.

    That is a claim about today's code, not a property of logging. If a
    future change logs a raw exception from app/integrations/, this log
    becomes the leak, and the log directory is gitignored precisely so
    that it is never the thing that gets committed or packed.

.PARAMETER Time
    Daily start time, HH:mm. Default 03:00 -- after midnight so a run
    covers a full day of postings, and early enough that a full
    enrichment pass (27-84 minutes) finishes before anyone looks.

.PARAMETER TaskName
    Scheduled Task name. Default "AIJobHuntAgent".

.PARAMETER SelfTest
    Prove the mechanism without arming anything. Verifies the venv
    interpreter exists and is the one that will be used, verifies the
    repo root resolves, verifies the log directory is writable and
    gitignored, and runs the agent's --help through the exact command
    line the task would use -- then registers nothing and deletes
    nothing. Run this before ever registering for real.

.PARAMETER Unregister
    Remove the scheduled task. Leaves logs alone.

.EXAMPLE
    powershell -File scripts/schedule_agent.ps1 -SelfTest

.EXAMPLE
    powershell -File scripts/schedule_agent.ps1 -Time 03:00
#>

[CmdletBinding()]
param(
    [string]$Time = "03:00",
    [string]$TaskName = "AIJobHuntAgent",
    [switch]$SelfTest,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    $root = git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) {
        throw "Not inside a git repository."
    }
    return (Resolve-Path $root).Path
}

$RepoRoot = Get-RepoRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
$Runner = Join-Path $RepoRoot "scripts\run_nightly.ps1"

function Assert-Prerequisites {
    if (-not (Test-Path $VenvPython)) {
        throw "No venv interpreter at $VenvPython. Create it before scheduling."
    }
    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir | Out-Null
    }
    # A log directory that is not ignored is a log directory that ends up
    # in a commit and then in an archive. Checked, not assumed.
    $ignored = git -C $RepoRoot check-ignore "logs/probe.log" 2>$null
    if ([string]::IsNullOrWhiteSpace($ignored)) {
        throw "logs/ is not gitignored. Add it before scheduling: an unattended log is the one file nobody reviews before packing."
    }
}

function Write-Runner {
    # A separate .ps1 rather than a long -Argument string on the task
    # itself. Scheduled Task arguments are quoted by the task scheduler,
    # then re-parsed by PowerShell, then again by python -m; every layer
    # is a chance to lose a quote. A file has none of those layers, and
    # it can be run by hand to reproduce exactly what the task does.
    $body = @'
# Generated by scripts/schedule_agent.ps1. Edit that, not this.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$log = Join-Path $root "logs\agent_$stamp.log"

"=== started $(Get-Date -Format o) ===" | Out-File -FilePath $log -Encoding utf8

# 2>&1 merges stderr into the same stream so a traceback and the summary
# land in one file in the order they happened. A separate stderr file
# would interleave wrongly and hide which line caused which.
& "$root\.venv\Scripts\python.exe" -m scripts.run_agent *>&1 |
    Out-File -FilePath $log -Encoding utf8 -Append

$code = $LASTEXITCODE

# The exit code is the last line, on purpose. run_agent.py returns 1 on
# `degraded`, and a scheduled run whose exit code is not written down
# reports nothing at all -- it looks like it worked.
"=== finished $(Get-Date -Format o) exit=$code ===" |
    Out-File -FilePath $log -Encoding utf8 -Append

exit $code
'@
    Set-Content -Path $Runner -Value $body -Encoding utf8
}

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "unregistered        $TaskName"
    return
}

if ($SelfTest) {
    Assert-Prerequisites
    Write-Runner

    Write-Output "repo root           $RepoRoot"
    Write-Output "venv interpreter    $VenvPython"
    Write-Output "log directory       $LogDir (gitignored)"
    Write-Output "runner              $Runner"

    # The interpreter the task WILL use, asked directly rather than
    # inferred -- this is the check that would have caught the PATH
    # problem described at the top.
    $reported = & $VenvPython -c "import sys; print(sys.executable)"
    Write-Output "sys.executable      $reported"
    if ($reported -ne $VenvPython) {
        throw "venv interpreter reports a different sys.executable: $reported"
    }

    # Exercise the real command line, without doing any work.
    & $VenvPython -m scripts.run_agent --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "run_agent --help failed under the venv interpreter."
    }

    Write-Output ""
    Write-Output "self-test result   PASS"
    Write-Output "registered         nothing (self-test arms no task)"
    return
}

Assert-Prerequisites
Write-Runner

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

# StartWhenAvailable covers a machine that was asleep at $Time: the run
# happens late rather than not at all. It does NOT cover a machine that
# was off all day -- see "Not handled" below.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Nightly AI job hunt workflow. See scripts/schedule_agent.ps1." `
    -Force | Out-Null

Write-Output "registered          $TaskName"
Write-Output "daily at            $Time"
Write-Output "runs                $Runner"
Write-Output "logs to             $LogDir\agent_<timestamp>.log"

<#
NOT HANDLED, and named rather than quietly left out.

  Overlapping runs -- HANDLED, by -MultipleInstances IgnoreNew. A second
  start while the first is still going is dropped. Chosen over Queue
  because two concurrent runs would both call run_scoring against the
  same rows, and the second would spend Adzuna quota re-fetching what
  the first is already inserting. Dropping is the cheaper wrong answer,
  and agent_runs makes the drop visible after the fact: one row where
  two were expected.

  Missed runs while the machine was off -- PARTLY. StartWhenAvailable
  catches a machine asleep at 03:00 and runs late. It does not catch a
  machine that was off for a week: Windows fires one catch-up run, not
  seven, and nothing here distinguishes "ran once, late" from "ran
  once, on time". agent_runs.started_at is what a person would read to
  tell those apart, and doing better needs a schedule-vs-actual
  reconciliation that belongs with the Day 11 status work rather than
  here.

  Log growth -- NOT HANDLED. One file per run, forever, roughly 3 KB
  each. That is about 1 MB a year, which is why it is last on this list
  rather than solved: a rotation scheme is code that can delete the
  evidence of the run you most wanted to read, and 1 MB/year does not
  buy that risk. Revisit when a run starts logging per-job lines.
#>
