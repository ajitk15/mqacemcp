<#
.SYNOPSIS
    Stops everything launched by start-all.ps1.

.DESCRIPTION
    Reads scripts/.pids (written by start-all.ps1) and terminates each
    PowerShell window plus its child processes (python / streamlit). Safe
    to run multiple times - missing PIDs are reported, not errored.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$PidFile = Join-Path $PSScriptRoot ".pids"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)  { Write-Host "  !!  $msg" -ForegroundColor Yellow }

if (-not (Test-Path $PidFile)) {
    Write-Bad "No .pids file at $PidFile - nothing to stop."
    Write-Bad "If services are still running, kill them by closing their windows or via Task Manager."
    exit 0
}

$pids = Get-Content $PidFile | Where-Object { $_ -match '^\d+$' }
if (-not $pids) {
    Write-Bad ".pids is empty."
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

foreach ($processId in $pids) {
    Write-Step "Stopping PID $processId (and children)"
    # taskkill /T kills the whole process tree (PowerShell window + python/node children).
    & taskkill /PID $processId /T /F 2>&1 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "PID $processId terminated"
    } else {
        Write-Bad "PID $processId not found (already stopped?)"
    }
}

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Ok "All recorded services have been signalled."
