<#
    Scheduled-task entry point for the ledger fetchers.

    Runs every section, appending to data/fetch.log. Mirrors the jobs module's
    refresh_jobs.ps1 so both modules are operated the same way.

    Register (weekly, Monday 07:30):
      $py = "c:\DATA\hub\.venv\Scripts\python.exe"
      schtasks /create /tn "ledger-fetch-refresh" /sc weekly /d MON /st 07:30 `
        /tr "powershell -NoProfile -ExecutionPolicy Bypass -File c:\DATA\ledger\scripts\refresh_ledger.ps1"

    Set CONGRESS_API_KEY as a *machine* environment variable so the task sees it;
    a user-session variable won't be visible to a task running unattended.
#>
[CmdletBinding()]
param(
    [string[]]$Sections = @(),
    [string]$Python = "c:\DATA\hub\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repo "data"
$log = Join-Path $logDir "fetch.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $Python)) {
    "[$(Get-Date -Format s)] ERROR: python not found at $Python" | Add-Content $log
    exit 1
}

"[$(Get-Date -Format s)] --- refresh starting ---" | Add-Content $log

# Run from the repo so a relative import can't surprise us; the module resolves
# its own data path relative to the package regardless.
Push-Location $repo
try {
    # Python's logging writes to stderr, and under Windows PowerShell 5.1 an
    # ErrorActionPreference of 'Stop' promotes any native-command stderr line to a
    # terminating NativeCommandError. That would abort this script on the
    # fetcher's ordinary INFO logs, so the preference is relaxed for the call and
    # success is judged by the exit code instead.
    $ErrorActionPreference = "Continue"
    & $Python -m ledger.fetch @Sections 2>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

"[$(Get-Date -Format s)] --- refresh finished, exit=$code ---" | Add-Content $log

# Non-zero only on a real error. A section skipped for a missing API key exits 0
# on purpose, so the task doesn't report failure for an expected state.
exit $code
