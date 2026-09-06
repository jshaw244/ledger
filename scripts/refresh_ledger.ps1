<#
    Scheduled-task entry point for the ledger fetchers.

    Runs every section, appending to data/fetch.log. Mirrors the jobs module's
    refresh_jobs.ps1 so both modules are operated the same way.

    Registered as a daily task at 07:30 (jobs' weekly fetch runs at 07:00 Mondays;
    ledger is daily because FRED releases, roll-call votes, and FEC filings all
    move faster than weekly, and a full run costs only a handful of requests):

      schtasks /create /tn "ledger-fetch-refresh" /sc daily /st 07:30 `
        /tr "powershell -NoProfile -ExecutionPolicy Bypass -File c:\DATA\ledger\scripts\refresh_ledger.ps1"

    Change the cadence with:  schtasks /change /tn "ledger-fetch-refresh" ...
    Run it now with:          schtasks /run /tn "ledger-fetch-refresh"

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

# Every write to the log goes through here with an explicit encoding. Mixing
# writers is what corrupts it: under Windows PowerShell 5.1, Tee-Object defaults
# to UTF-16 while Add-Content defaults to ANSI, so a log written by both comes
# back as interleaved null bytes and mangled non-ASCII.
function Write-Log {
    param([Parameter(ValueFromPipeline = $true)] $Message)
    process { $Message | Out-String -Stream | Add-Content -Path $log -Encoding utf8 }
}

if (-not (Test-Path $Python)) {
    "[$(Get-Date -Format s)] ERROR: python not found at $Python" | Write-Log
    exit 1
}

"[$(Get-Date -Format s)] --- refresh starting ---" | Write-Log

# Run from the repo so a relative import can't surprise us; the module resolves
# its own data path relative to the package regardless.
Push-Location $repo
$prevEncoding = [Console]::OutputEncoding
try {
    # PowerShell decodes a native command's stdout using the console code page,
    # not the encoding the program actually wrote. Python emits UTF-8, so without
    # this the fetchers' em dashes and middle dots reach the log already corrupted
    # — before any of our own encoding handling gets a say.
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"

    # Python's logging writes to stderr, and under Windows PowerShell 5.1 an
    # ErrorActionPreference of 'Stop' promotes any native-command stderr line to a
    # terminating NativeCommandError. That would abort this script on the
    # fetcher's ordinary INFO logs, so the preference is relaxed for the call and
    # success is judged by the exit code instead.
    $ErrorActionPreference = "Continue"
    $output = & $Python -m ledger.fetch @Sections 2>&1 | ForEach-Object { $_.ToString() }
    $code = $LASTEXITCODE
    $output | Write-Log
    $output | Write-Host      # still visible when run by hand
}
finally {
    [Console]::OutputEncoding = $prevEncoding
    Pop-Location
}

"[$(Get-Date -Format s)] --- refresh finished, exit=$code ---" | Write-Log

# Non-zero only on a real error. A section skipped for a missing API key exits 0
# on purpose, so the task doesn't report failure for an expected state.
exit $code
