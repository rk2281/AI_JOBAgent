<#
.SYNOPSIS
    Register (or self-test) the nightly and weekly workflow runs as
    Windows Scheduled Tasks.

.DESCRIPTION
    scripts/run_agent.py already runs the whole graph and already exits
    non-zero when something went wrong. What it does not do is run when
    nobody types the command, and that is all this script adds.

    TWO TASKS, NOT ONE, AS OF DAY 13

    Ingestion inserts a fixed ~100 jobs/run; enrichment (rationed by a
    separate, much scarcer Gemini quota -- see app/core/config.py's
    enrichment_seconds_between_calls comment) has been clearing 1-20 a
    run. Every night both ran together, the backlog grew every night,
    and it never converges: see CLAUDE.md section 10.

    The fix is to decouple cadence, not code: scripts/run_agent.py
    already has --skip-ingestion and --skip-enrichment, tested, doing
    nothing new. This script now registers:

      AIJobHuntAgent           daily at -Time (default 03:00)
                                --skip-ingestion
                                embed_jobs + enrich_jobs + score_and_rank

      AIJobHuntAgentIngestion  weekly at -WeeklyDay/-WeeklyTime
                                (default Sunday 02:00)
                                --skip-enrichment
                                discover_jobs + embed_jobs + score_and_rank

    TWO TASKS RATHER THAN ONE TASK WITH A DAY-OF-WEEK BRANCH

    The alternative was a single task whose wrapper script checks
    (Get-Date).DayOfWeek and decides which flag to pass. Rejected,
    because it fails the wrong way: a broken day-of-week check still
    runs the graph, still exits 0 or 1, still writes a normal-looking
    log every single night -- section 0's "a success status is not
    success" exactly. Telling the two apart needs a human to read a log
    and separately remember what day it was. A missing or wrong weekly
    TRIGGER, by contrast, is visible in the trigger itself:
    Get-ScheduledTaskInfo on the named task answers "did the weekly one
    run" directly, with no code to reason about and no log to open. It
    also matches how this project already treats differently-rationed
    concerns -- ingestion, embedding and enrichment are three separate
    services for the same reason (app/services/job_enrichment.py's own
    docstring), not one service with an if-branch.

    The cost is that both tasks need the same LogonType S4U principal
    (see below), which a single shared Register-AgentTask function
    writes once rather than twice -- two tasks calling one function
    cannot drift the way two copy-pasted registration blocks could.

    WHY 02:00 AND 03:00, NOT THE SAME TIME

    Observed 2026-09-05/06: a full run WITH enrichment (17 calls before
    quota) took ~11 minutes end to end; ingestion + embedding alone
    (no enrichment) completed in roughly 2-4 minutes on every observed
    run. An hour of separation is generous headroom, not a tight fit,
    so the weekly ingestion pass should always finish well before the
    nightly one starts. Not proven under load -- see "NOT HANDLED"
    below for the residual risk this leaves.

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
       a degraded status, and that number is the whole point of the
       wrapper below -- it captures stdout, stderr AND $LASTEXITCODE,
       and writes the exit code as the last line of the log.

    NO --dry-run on either task. A scheduled dry run is a scheduled
    no-op that reports a healthy status every morning while doing
    nothing -- section 0 in its purest form. Both tasks run for real.

    WHAT CAN END UP IN THE LOG

    Checked before writing a line, because CLAUDE.md section 3 records
    that nine of eleven incidents came from a secret handled
    incidentally while doing something else, and an unattended log is
    exactly that shape. The weekly task invokes app/integrations/adzuna.py,
    and Adzuna's app_id and app_key travel as QUERY PARAMETERS, so any
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
    Nightly start time, HH:mm. Default 03:00 -- after midnight so a run
    covers a full day of postings, and comfortably after the weekly
    ingestion task's 02:00 so the two never race.

.PARAMETER TaskName
    Nightly task name. Default "AIJobHuntAgent".

.PARAMETER WeeklyDay
    Day of week the ingestion task fires. Default "Sunday".

.PARAMETER WeeklyTime
    Weekly ingestion start time, HH:mm. Default 02:00.

.PARAMETER IngestionTaskName
    Weekly ingestion task name. Default "AIJobHuntAgentIngestion".

.PARAMETER SelfTest
    Prove the mechanism without arming anything. Verifies the venv
    interpreter exists and is the one that will be used, verifies the
    repo root resolves, verifies the log directory is writable and
    gitignored, writes BOTH generated runner scripts, and runs the
    agent's --help through the exact interpreter both tasks use --
    then registers nothing and deletes nothing. Run this before ever
    registering for real.

.PARAMETER Unregister
    Remove both scheduled tasks. Leaves logs alone.

.EXAMPLE
    powershell -File scripts/schedule_agent.ps1 -SelfTest

.EXAMPLE
    powershell -File scripts/schedule_agent.ps1
#>

[CmdletBinding()]
param(
    [string]$Time = "03:00",
    [string]$TaskName = "AIJobHuntAgent",
    [string]$WeeklyDay = "Sunday",
    [string]$WeeklyTime = "02:00",
    [string]$IngestionTaskName = "AIJobHuntAgentIngestion",
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
$NightlyRunner = Join-Path $RepoRoot "scripts\run_nightly.ps1"
$WeeklyRunner = Join-Path $RepoRoot "scripts\run_weekly_ingestion.ps1"

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
    #
    # Single-quoted (literal) template with two placeholders, filled in
    # by plain string substitution rather than here-string interpolation.
    # Interpolating into a template that ALSO contains this generated
    # script's own $root/$stamp/$log/$code would need every one of
    # those escaped so THIS script does not evaluate them at generation
    # time -- and a missed escape fails silently, evaluating to an
    # empty string here rather than erroring. Substitution after the
    # fact has no escaping to get right in the first place.
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$ExtraArgs,
        [Parameter(Mandatory)] [string]$LogPrefix
    )

    $template = @'
# Generated by scripts/schedule_agent.ps1. Edit that, not this.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$log = Join-Path $root "logs\@@LOG_PREFIX@@_$stamp.log"

"=== started $(Get-Date -Format o) ===" | Out-File -FilePath $log -Encoding utf8

# 2>&1 merges stderr into the same stream so a traceback and the summary
# land in one file in the order they happened. A separate stderr file
# would interleave wrongly and hide which line caused which.
& "$root\.venv\Scripts\python.exe" -m scripts.run_agent @@EXTRA_ARGS@@ *>&1 |
    Out-File -FilePath $log -Encoding utf8 -Append

$code = $LASTEXITCODE

# The exit code is the last line, on purpose. run_agent.py returns 1 on
# a degraded status, and a scheduled run whose exit code is not written
# down reports nothing at all -- it looks like it worked.
"=== finished $(Get-Date -Format o) exit=$code ===" |
    Out-File -FilePath $log -Encoding utf8 -Append

exit $code
'@

    $body = $template.Replace('@@LOG_PREFIX@@', $LogPrefix).Replace('@@EXTRA_ARGS@@', $ExtraArgs)
    Set-Content -Path $Path -Value $body -Encoding utf8
}

function Register-AgentTask {
    # ONE function registers BOTH tasks so the principal below is
    # written once, not twice. Two tasks calling one function cannot
    # drift the way two copy-pasted registration blocks could -- which
    # is the specific cost this design accepted in exchange for two
    # independently observable schedules. See the .DESCRIPTION block.
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] $Trigger,
        [Parameter(Mandatory)] [string]$RunnerPath,
        [Parameter(Mandatory)] [string]$Description
    )

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`"" `
        -WorkingDirectory $RepoRoot

    # StartWhenAvailable covers a machine that was asleep at the trigger
    # time: the run happens late rather than not at all. IgnoreNew drops
    # a second start of the SAME task while the first is still going --
    # see "NOT HANDLED" below for what it does NOT cover across the two
    # DIFFERENTLY NAMED tasks this script now registers.
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 3)

    # No -Principal means Register-ScheduledTask defaults to LogonType
    # Interactive, which only runs the task inside an interactive logon
    # session for $env:USERNAME -- a different contract from "runs
    # unattended overnight". Verified 2026-09-05 by reading the LIVE
    # registered task (Get-ScheduledTask ... | select Principal), not
    # the script: LogonType was Interactive and the first unattended
    # fire returned 3221225786 (0xC000013A). S4U runs unattended for
    # this user without storing a password, which -Password would need.
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $Name `
        -Action $action `
        -Trigger $Trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $Description `
        -Force | Out-Null
}

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $IngestionTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "unregistered        $TaskName"
    Write-Output "unregistered        $IngestionTaskName"
    return
}

if ($SelfTest) {
    Assert-Prerequisites
    Write-Runner -Path $NightlyRunner -ExtraArgs "--skip-ingestion" -LogPrefix "agent"
    Write-Runner -Path $WeeklyRunner -ExtraArgs "--skip-enrichment" -LogPrefix "agent_ingestion"

    Write-Output "repo root                $RepoRoot"
    Write-Output "venv interpreter         $VenvPython"
    Write-Output "log directory            $LogDir (gitignored)"
    Write-Output "nightly runner           $NightlyRunner (--skip-ingestion)"
    Write-Output "weekly ingestion runner  $WeeklyRunner (--skip-enrichment)"

    # The interpreter the tasks WILL use, asked directly rather than
    # inferred -- this is the check that would have caught the PATH
    # problem described at the top. Both tasks use the same interpreter,
    # so one check covers both.
    $reported = & $VenvPython -c "import sys; print(sys.executable)"
    Write-Output "sys.executable           $reported"
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
Write-Runner -Path $NightlyRunner -ExtraArgs "--skip-ingestion" -LogPrefix "agent"
Write-Runner -Path $WeeklyRunner -ExtraArgs "--skip-enrichment" -LogPrefix "agent_ingestion"

$nightlyTrigger = New-ScheduledTaskTrigger -Daily -At $Time
$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $WeeklyDay -At $WeeklyTime

Register-AgentTask `
    -Name $TaskName `
    -Trigger $nightlyTrigger `
    -RunnerPath $NightlyRunner `
    -Description "Nightly enrichment+scoring pass (--skip-ingestion). See scripts/schedule_agent.ps1."

Register-AgentTask `
    -Name $IngestionTaskName `
    -Trigger $weeklyTrigger `
    -RunnerPath $WeeklyRunner `
    -Description "Weekly ingestion+embedding pass (--skip-enrichment). See scripts/schedule_agent.ps1."

Write-Output "registered          $TaskName"
Write-Output "daily at            $Time"
Write-Output "runs                $NightlyRunner (--skip-ingestion)"
Write-Output ""
Write-Output "registered          $IngestionTaskName"
Write-Output "weekly at           $WeeklyDay $WeeklyTime"
Write-Output "runs                $WeeklyRunner (--skip-enrichment)"
Write-Output ""
Write-Output "logs to             $LogDir\agent_<timestamp>.log (nightly)"
Write-Output "                    $LogDir\agent_ingestion_<timestamp>.log (weekly)"

<#
NOT HANDLED, and named rather than quietly left out.

  Overlapping runs of the SAME task -- HANDLED, by -MultipleInstances
  IgnoreNew. A second start of AIJobHuntAgent while the first is still
  going is dropped, and likewise for AIJobHuntAgentIngestion against
  itself.

  Overlapping runs ACROSS the two DIFFERENT tasks -- NOT HANDLED, and
  this is new as of the two-task split. IgnoreNew is scoped per task
  name, so nothing stops AIJobHuntAgentIngestion from still running
  past its 02:00 start when AIJobHuntAgent fires at 03:00. Both would
  then be free to call embed_jobs concurrently, and there is no
  distributed lock on which jobs get claimed. Not exercised: every
  observed ingestion+embedding-only run has finished in a few minutes,
  well inside the hour of separation, but "well inside" is a margin
  from observation, not a guarantee. If a Sunday log ever shows the
  nightly task starting before the weekly one has finished, that is
  the thing to look at first, before touching scoring or enrichment.

  Missed runs while the machine was off -- PARTLY. StartWhenAvailable
  catches a machine asleep at trigger time and runs late. It does not
  catch a machine that was off for a week: Windows fires one catch-up
  run, not seven, and nothing here distinguishes "ran once, late" from
  "ran once, on time". agent_runs.started_at is what a person would
  read to tell those apart, and doing better needs a schedule-vs-actual
  reconciliation that belongs with the Day 11 status work rather than
  here.

  Log growth -- NOT HANDLED. One file per run, forever, roughly 3 KB
  each, now from two tasks instead of one. Still not worth a rotation
  scheme that could delete the evidence of the run most worth reading.
  Revisit when a run starts logging per-job lines.
#>
