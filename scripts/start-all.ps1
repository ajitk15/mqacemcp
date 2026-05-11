<#
.SYNOPSIS
    Starts the three local processes for the MCP chatbot stack.

.DESCRIPTION
    Opens three new PowerShell windows so each service has its own visible
    log stream:
      1. MCP server   (mqacemcpserver.py, SSE on :8000)
      2. Chat backend (FastAPI on :8001)
      3. Chat UI      (Next.js dev server on :3000)

    Pre-flight checks every prerequisite (venvs, .env files, node_modules)
    and refuses to start anything until they're satisfied — with a clear
    fix-up command for each missing piece.

    PIDs of spawned PowerShell windows are written to scripts/.pids so
    stop-all.ps1 can clean them up.

.PARAMETER SkipMcp
    Do not start the MCP server. Use this when you already have an MCP
    server running (locally on a different port, or remote). The chat
    backend will still be started — it reads MCP_SSE_URL from its own .env.

.PARAMETER SkipBackend
    Do not start the chat backend.

.PARAMETER SkipFrontend
    Do not start the chat UI.

.PARAMETER CheckOnly
    Run all pre-flight checks and exit without starting anything.

.EXAMPLE
    .\scripts\start-all.ps1

.EXAMPLE
    .\scripts\start-all.ps1 -SkipMcp
#>
[CmdletBinding()]
param(
    [switch]$SkipMcp,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location so the script works from any cwd.
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "chatbot\backend"
$FrontendDir= Join-Path $RepoRoot "chatbot\frontend"
$PidFile    = Join-Path $PSScriptRoot ".pids"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)   { Write-Host "  !!  $msg" -ForegroundColor Red }
function Write-Note($msg)  { Write-Host "      $msg" -ForegroundColor DarkGray }

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
$problems = @()

if (-not $SkipMcp) {
    Write-Step "Checking MCP server prerequisites"
    $mcpVenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $mcpEntry      = Join-Path $RepoRoot "mqacemcpserver.py"
    $mcpEnv        = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $mcpVenvPython)) {
        $problems += "Missing MCP venv. Fix: cd `"$RepoRoot`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad ".venv\Scripts\python.exe not found"
    } else { Write-Ok ".venv present" }
    if (-not (Test-Path $mcpEntry)) {
        $problems += "Missing mqacemcpserver.py at repo root."
        Write-Bad "mqacemcpserver.py not found"
    } else { Write-Ok "mqacemcpserver.py present" }
    if (-not (Test-Path $mcpEnv)) {
        Write-Note ".env missing at repo root (server will start but tools may error). Copy .env.example to .env and fill MQ_*/ACE_* values if you need real data."
    } else { Write-Ok ".env present" }
}

if (-not $SkipBackend) {
    Write-Step "Checking chat backend prerequisites"
    $beVenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    $beApp        = Join-Path $BackendDir "app.py"
    $beEnv        = Join-Path $BackendDir ".env"
    if (-not (Test-Path $beVenvPython)) {
        $problems += "Missing backend venv. Fix: cd `"$BackendDir`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad "chatbot\backend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "backend venv present" }
    if (-not (Test-Path $beApp)) {
        $problems += "Missing chatbot\backend\app.py."
        Write-Bad "chatbot\backend\app.py not found"
    } else { Write-Ok "backend app.py present" }
    if (-not (Test-Path $beEnv)) {
        $problems += "Missing chatbot\backend\.env. Fix: cd `"$BackendDir`" ; copy .env.example .env ; then edit it (OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*)"
        Write-Bad "chatbot\backend\.env not found"
    } else { Write-Ok "backend .env present" }
}

if (-not $SkipFrontend) {
    Write-Step "Checking chat UI prerequisites"
    $fePkg          = Join-Path $FrontendDir "package.json"
    $feNodeModules  = Join-Path $FrontendDir "node_modules"
    $feEnv          = Join-Path $FrontendDir ".env.local"
    if (-not (Test-Path $fePkg)) {
        $problems += "Missing chatbot\frontend\package.json."
        Write-Bad "chatbot\frontend\package.json not found"
    } else { Write-Ok "frontend package.json present" }
    if (-not (Test-Path $feNodeModules)) {
        $problems += "Missing frontend node_modules. Fix: cd `"$FrontendDir`" ; npm install"
        Write-Bad "chatbot\frontend\node_modules not found"
    } else { Write-Ok "node_modules present" }
    if (-not (Test-Path $feEnv)) {
        Write-Note "chatbot\frontend\.env.local missing — defaults to BACKEND_URL=http://localhost:8001. Copy .env.local.example if you need to override."
    } else { Write-Ok "frontend .env.local present" }
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Bad "Pre-flight failed. Resolve the items above before running start-all again:"
    $problems | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    exit 1
}

if ($CheckOnly) {
    Write-Host ""
    Write-Ok "All checks passed. (CheckOnly was specified, not starting services.)"
    exit 0
}

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
$pids = @()

function Start-Service-Window {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )
    Write-Step "Starting $Title"
    Write-Note "cwd: $WorkingDirectory"
    Write-Note "cmd: $Command"
    # -NoExit keeps the window open so logs remain visible. The window title
    # is set with $Host.UI.RawUI inside the spawned shell.
    $script = "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoExit", "-NoLogo", "-Command", $script) `
        -WorkingDirectory $WorkingDirectory `
        -PassThru
    Write-Ok "$Title PID=$($proc.Id)"
    return $proc.Id
}

if (-not $SkipMcp) {
    $cmd = "`$env:MCP_TRANSPORT='sse'; .\.venv\Scripts\python.exe mqacemcpserver.py"
    $pids += Start-Service-Window -Title "MCP Server (SSE :8000)" `
        -WorkingDirectory $RepoRoot -Command $cmd
    Start-Sleep -Seconds 2  # let it bind before backend tries to connect
}

if (-not $SkipBackend) {
    $cmd = ".\.venv\Scripts\python.exe app.py"
    $pids += Start-Service-Window -Title "Chat Backend (FastAPI :8001)" `
        -WorkingDirectory $BackendDir -Command $cmd
    Start-Sleep -Seconds 2  # let backend load tools before frontend starts hitting it
}

if (-not $SkipFrontend) {
    $cmd = "npm run dev"
    $pids += Start-Service-Window -Title "Chat UI (Next.js :3000)" `
        -WorkingDirectory $FrontendDir -Command $cmd
}

# Persist PIDs so stop-all.ps1 can find them.
$pids | Out-File -FilePath $PidFile -Encoding ascii

Write-Host ""
Write-Ok "All requested services launched."
Write-Host ""
Write-Host "  MCP health    : http://localhost:8000/healthz" -ForegroundColor Gray
Write-Host "  Backend health: http://localhost:8001/api/health" -ForegroundColor Gray
Write-Host "  Chat UI       : http://localhost:3000" -ForegroundColor Gray
Write-Host ""
Write-Host "  To stop everything, run:  .\scripts\stop-all.ps1" -ForegroundColor DarkGray
